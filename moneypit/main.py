from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Form, Query, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .db import connect, init_db
from .importers import parse_csv
from .categorize import apply_rules
from .recurring import detect_recurring
from .rules import suggest_pattern, create_rule_and_recategorize

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Moneypit", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()


def _load_categories() -> list[str]:
    with connect() as conn:
        return [r["name"] for r in conn.execute("SELECT name FROM categories ORDER BY name")]


# Palette used to auto-color new user-created categories. Cycles through these
# so fresh categories get a distinct swatch without the user having to pick.
_NEW_CATEGORY_PALETTE = [
    "#f43f5e", "#ec4899", "#a855f7", "#8b5cf6", "#6366f1",
    "#3b82f6", "#06b6d4", "#14b8a6", "#22c55e", "#84cc16",
    "#eab308", "#f59e0b", "#ef4444",
]


def _resolve_category(conn, category: str, category_new: str) -> str:
    """
    Decide the final category string for a form submission that may include
    a "create new" option. Returns the category name (empty string if neither
    field was provided). Creates the category if it's new.

    Accepts either an existing category from the dropdown, or the sentinel
    `__new__` paired with a free-text name in `category_new`. Whitespace is
    trimmed; blank "new" values fall back to the dropdown value.
    """
    new_name = category_new.strip()
    if category == "__new__" and new_name:
        existing = conn.execute(
            "SELECT name FROM categories WHERE LOWER(name) = LOWER(?)",
            (new_name,),
        ).fetchone()
        if existing:
            return existing["name"]
        # Pick the first palette color not already taken, so repeated
        # additions don't all land on the same hue.
        used = {r["color"] for r in conn.execute("SELECT color FROM categories")}
        color = next((c for c in _NEW_CATEGORY_PALETTE if c not in used), _NEW_CATEGORY_PALETTE[0])
        conn.execute("INSERT INTO categories (name, color) VALUES (?, ?)", (new_name, color))
        return new_name
    # If the user picked __new__ but left the text empty, treat it as "no
    # selection" so the caller can fall back (e.g. to rules auto-detection).
    if category == "__new__":
        return ""
    return category


def _load_profiles() -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, name, color FROM profiles ORDER BY id"
        )]


def _resolve_profile(profile: str | None) -> tuple[int | None, str]:
    """
    Parse ?profile= query param. Returns (profile_id, label).
    "" or missing → (None, "All profiles") — the combined-budget view.
    Numeric string → (int, profile name) if it exists, else the default.
    """
    if not profile:
        return None, "All profiles"
    try:
        pid = int(profile)
    except ValueError:
        return None, "All profiles"
    with connect() as conn:
        row = conn.execute("SELECT name FROM profiles WHERE id = ?", (pid,)).fetchone()
    if row is None:
        return None, "All profiles"
    return pid, row["name"]


# Fallback only — "All time" actually resolves to the earliest recorded
# transaction date (see `_earliest_transaction_date`). This sentinel is used
# when the DB is empty and we need *some* lower bound.
DEFAULT_ALL_TIME_START = date(1970, 1, 1)


def _earliest_transaction_date() -> date:
    """Earliest `date` across all transactions, or today if the table is empty.

    Used as the lower bound for "All time" ranges and the `min` on date
    pickers, so users can't scroll back through decades of empty calendar.
    """
    with connect() as conn:
        row = conn.execute("SELECT MIN(date) AS d FROM transactions").fetchone()
    if row is None or row["d"] is None:
        return date.today()
    try:
        return date.fromisoformat(row["d"])
    except ValueError:
        return DEFAULT_ALL_TIME_START


def _resolve_range(
    from_: str | None,
    to: str | None,
    default: str = "last30",
    earliest: date | None = None,
) -> tuple[date, date, str]:
    """
    Parse ?from= / ?to= query params into concrete dates. When both are
    missing, falls back to `default`:
      - "last30"  -> last 30 days ending today (dashboard default)
      - "all"     -> all time (transactions browser default) — uses the
                     earliest recorded transaction date rather than 1970
    Returns (start, end, human_label).
    """
    today = date.today()
    all_time_start = earliest if earliest is not None else DEFAULT_ALL_TIME_START
    try:
        end = date.fromisoformat(to) if to else today
    except ValueError:
        end = today
    try:
        if from_:
            start = date.fromisoformat(from_)
        elif default == "all":
            start = all_time_start
        else:
            start = end - timedelta(days=29)
    except ValueError:
        start = end - timedelta(days=29) if default != "all" else all_time_start

    if start > end:
        start, end = end, start

    # Human label
    if not from_ and not to:
        label = "All time" if default == "all" else "Last 30 days"
    elif start == end:
        label = start.strftime("%d %b %Y")
    elif start.year == end.year and start.month == end.month:
        label = f"{start.strftime('%d')}–{end.strftime('%d %b %Y')}"
    else:
        label = f"{start.strftime('%d %b %Y')} – {end.strftime('%d %b %Y')}"

    return start, end, label


def _build_presets(today: date, earliest: date | None = None) -> list[tuple[str, str, str]]:
    """Quick-pick date ranges shared by dashboard and transactions pages."""
    first_of_this_month = today.replace(day=1)
    last_of_prev_month = first_of_this_month - timedelta(days=1)
    first_of_prev_month = last_of_prev_month.replace(day=1)
    all_time_start = earliest if earliest is not None else DEFAULT_ALL_TIME_START
    return [
        ("Last 30 days", "",                                 ""),
        ("This month",   first_of_this_month.isoformat(),    today.isoformat()),
        ("Last month",   first_of_prev_month.isoformat(),    last_of_prev_month.isoformat()),
        ("This year",    date(today.year, 1, 1).isoformat(), today.isoformat()),
        ("All time",     all_time_start.isoformat(),         today.isoformat()),
    ]


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None, alias="to"),
    profile: str | None = Query(None),
):
    earliest = _earliest_transaction_date()
    start, end, range_label = _resolve_range(from_, to, earliest=earliest)
    start_s, end_s = start.isoformat(), end.isoformat()
    profile_id, profile_label = _resolve_profile(profile)

    # Base WHERE for date-filtered queries. Profile is optional — when None,
    # show the combined household view (default). When set, pin to one profile.
    prof_clause = " AND profile_id = ?" if profile_id is not None else ""
    prof_params: list = [profile_id] if profile_id is not None else []

    with connect() as conn:
        by_category = conn.execute(
            f"""SELECT category, SUM(-amount) AS total, COUNT(*) AS n
               FROM transactions
               WHERE amount < 0 AND date BETWEEN ? AND ?{prof_clause}
               GROUP BY category
               ORDER BY total DESC""",
            [start_s, end_s, *prof_params],
        ).fetchall()

        top_vendors = conn.execute(
            f"""SELECT vendor, SUM(-amount) AS total, COUNT(*) AS n
               FROM transactions
               WHERE amount < 0 AND date BETWEEN ? AND ? AND vendor IS NOT NULL{prof_clause}
               GROUP BY vendor
               ORDER BY total DESC
               LIMIT 10""",
            [start_s, end_s, *prof_params],
        ).fetchall()

        recent = conn.execute(
            f"""SELECT id, date, amount, currency, vendor, description, category
               FROM transactions
               WHERE date BETWEEN ? AND ?{prof_clause}
               ORDER BY date DESC, id DESC
               LIMIT 25""",
            [start_s, end_s, *prof_params],
        ).fetchall()

        # Uncategorized intentionally ignores the date filter — if you have
        # an uncategorized transaction from 3 months ago, you still want
        # the chance to fix it without juggling date pickers.
        uncategorized = conn.execute(
            f"""SELECT id, date, amount, currency, vendor, description, category
               FROM transactions
               WHERE category = 'Uncategorized'{prof_clause}
               ORDER BY ABS(amount) DESC, date DESC
               LIMIT 25""",
            prof_params,
        ).fetchall()

        # Exclude Transfers from income/spend totals — moving money between
        # your own accounts or repaying a credit card isn't real income or
        # real spending, and counting it double-inflates both sides.
        totals = conn.execute(
            f"""SELECT
                   COALESCE(SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END), 0) AS spent,
                   COALESCE(SUM(CASE WHEN amount > 0 THEN  amount ELSE 0 END), 0) AS income
               FROM transactions
               WHERE date BETWEEN ? AND ? AND COALESCE(category, '') != 'Transfers'{prof_clause}""",
            [start_s, end_s, *prof_params],
        ).fetchone()
        total_spend = totals["spent"]
        total_income = totals["income"]
        net = total_income - total_spend

    recurring = detect_recurring(profile_id=profile_id)  # all-time, intentionally
    categories = _load_categories()
    profiles = _load_profiles()
    presets = _build_presets(date.today(), earliest=earliest)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "range_label": range_label,
            "range_from": start_s,
            "range_to": end_s,
            "range_min": earliest.isoformat(),
            "is_default_range": not from_ and not to,
            "presets": presets,
            "profile_id": profile_id,
            "profile_label": profile_label,
            "profiles": profiles,
            "total_spend": total_spend,
            "total_income": total_income,
            "net": net,
            "by_category": [dict(r) for r in by_category],
            "top_vendors": [dict(r) for r in top_vendors],
            "recent": [dict(r) for r in recent],
            "uncategorized": [dict(r) for r in uncategorized],
            "recurring": recurring,
            "categories": categories,
            "suggest_pattern": suggest_pattern,
        },
    )


@app.get("/import", response_class=HTMLResponse)
def import_page(
    request: Request,
    imported: int | None = None,
    skipped: int | None = None,
    bank: str | None = None,
    added: int | None = None,
    error: str | None = None,
):
    categories = _load_categories()
    profiles = _load_profiles()
    return templates.TemplateResponse(
        request,
        "import.html",
        {
            "categories": categories,
            "profiles": profiles,
            "imported": imported,
            "skipped": skipped,
            "bank": bank,
            "added": added,
            "error": error,
            "today": date.today().isoformat(),
        },
    )


@app.post("/import/csv")
async def import_csv(
    file: UploadFile = File(...),
    profile_id: str = Form(""),
):
    content = await file.read()
    try:
        bank, txs = parse_csv(content, source_ref=file.filename or "upload")
    except ValueError as e:
        return RedirectResponse(url=f"/import?error={e}", status_code=303)

    # Blank profile falls back to the default "Me" profile — we never want
    # un-tagged transactions now that the filter UI assumes everything has
    # a profile. Unknown IDs also fall back rather than silently drop.
    pid = _coerce_profile_id(profile_id)

    inserted = skipped = 0
    with connect() as conn:
        for tx in txs:
            apply_rules(tx, conn)
            tx.profile_id = pid
            try:
                conn.execute(
                    """INSERT INTO transactions
                       (date, amount, currency, description, vendor, category,
                        op_type, source, source_bank, source_ref, profile_id, hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        tx.date.isoformat(), tx.amount, tx.currency,
                        tx.description, tx.vendor, tx.category,
                        tx.op_type, tx.source, tx.source_bank, tx.source_ref,
                        tx.profile_id, tx.hash_key(),
                    ),
                )
                inserted += 1
            except Exception as e:
                if "UNIQUE" in str(e):
                    skipped += 1
                else:
                    raise

    return RedirectResponse(
        url=f"/import?imported={inserted}&skipped={skipped}&bank={bank}",
        status_code=303,
    )


def _coerce_profile_id(raw: str) -> int:
    """Resolve a form-submitted profile id to an existing profile. Falls back
    to the default 'Me' profile when empty or unknown — we always want a
    concrete profile stamped on new transactions."""
    from .db import DEFAULT_PROFILE_NAME
    with connect() as conn:
        if raw:
            try:
                pid = int(raw)
            except ValueError:
                pid = None
            if pid is not None:
                row = conn.execute("SELECT id FROM profiles WHERE id = ?", (pid,)).fetchone()
                if row is not None:
                    return row["id"]
        row = conn.execute(
            "SELECT id FROM profiles WHERE name = ?", (DEFAULT_PROFILE_NAME,)
        ).fetchone()
        return row["id"]


@app.post("/transactions")
def add_transaction(
    tx_date: str = Form(..., alias="date"),
    amount: float = Form(...),
    direction: str = Form("spend"),  # "spend" | "income"
    currency: str = Form("PLN"),
    vendor: str = Form(""),
    description: str = Form(""),
    category: str = Form(""),
    category_new: str = Form(""),
    profile_id: str = Form(""),
):
    """Create a transaction manually (cash purchase, receipt, reconciliation)."""
    import secrets
    from .models import Transaction as TxModel

    try:
        parsed_date = date.fromisoformat(tx_date)
    except ValueError:
        return RedirectResponse(url="/import?error=Invalid+date", status_code=303)

    # Amount comes in as positive from the form; sign is set by the direction
    # toggle. Reject 0 since it's almost certainly user error.
    amount = abs(amount)
    if amount == 0:
        return RedirectResponse(url="/import?error=Amount+cannot+be+zero", status_code=303)
    signed = amount if direction == "income" else -amount

    desc = description.strip() or vendor.strip() or "(manual entry)"
    pid = _coerce_profile_id(profile_id)

    with connect() as conn:
        resolved_category = _resolve_category(conn, category.strip(), category_new)
        tx = TxModel(
            date=parsed_date,
            amount=signed,
            currency=currency.strip().upper() or "PLN",
            description=desc,
            vendor=vendor.strip() or None,
            category=resolved_category or None,
            source="manual",
            profile_id=pid,
        )

        # Manual entries need a salted hash so that honest duplicates (two 15 PLN
        # coffees at the same place on the same day) don't collide with the
        # UNIQUE constraint. CSV imports rely on the deterministic hash for
        # idempotent re-imports, but manual submits are one-shot by nature.
        salted = f"manual|{secrets.token_hex(8)}|{tx.hash_key()}"

        if not tx.category:
            apply_rules(tx, conn)
        conn.execute(
            """INSERT INTO transactions
               (date, amount, currency, description, vendor, category,
                op_type, source, source_bank, source_ref, profile_id, hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tx.date.isoformat(), tx.amount, tx.currency,
                tx.description, tx.vendor, tx.category,
                tx.op_type, tx.source, None, None, tx.profile_id, salted,
            ),
        )

    return RedirectResponse(url="/import?added=1", status_code=303)


@app.post("/transactions/{tx_id}/categorize", response_class=HTMLResponse)
def categorize_transaction(
    request: Request,
    tx_id: int,
    category: str = Form(...),
    category_new: str = Form(""),
    pattern: str = Form(""),
    vendor: str = Form(""),
    create_rule: str = Form(""),
):
    """
    Inline categorize one transaction. If `create_rule` is set, also persist
    a rule from the given pattern so future charges auto-categorize, and
    retroactively apply it to existing transactions.

    Returns the updated row HTML (or a "vanished" placeholder if the row no
    longer belongs in the list it was rendered into — e.g. moved off the
    "Uncategorized" list).
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT id, description, vendor FROM transactions WHERE id = ?",
            (tx_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="transaction not found")

        resolved_category = _resolve_category(conn, category, category_new)
        if not resolved_category:
            # User picked "New…" but didn't type a name. Bail out without
            # mutating anything — the row stays in Uncategorized.
            raise HTTPException(status_code=400, detail="category name required")

        updated_count = 1
        if create_rule and pattern.strip():
            summary = create_rule_and_recategorize(
                conn, pattern.strip(), resolved_category, vendor.strip() or None
            )
            updated_count = summary["updated"]
        else:
            conn.execute(
                "UPDATE transactions SET category = ?, vendor = COALESCE(NULLIF(?, ''), vendor) WHERE id = ?",
                (resolved_category, vendor.strip(), tx_id),
            )

        updated = conn.execute(
            "SELECT id, date, amount, currency, vendor, description, category FROM transactions WHERE id = ?",
            (tx_id,),
        ).fetchone()

    categories = _load_categories()
    return templates.TemplateResponse(
        request,
        "_tx_row.html",
        {
            "t": dict(updated),
            "categories": categories,
            "flash_count": updated_count if create_rule else None,
            "suggest_pattern": suggest_pattern,
        },
    )


PAGE_SIZE = 50


# Column key -> SQL expression. Whitelist so `sort=` can't inject anything;
# the value is interpolated directly into ORDER BY. Each entry also says
# whether we sort by absolute value (makes "amount" put the biggest spends
# and biggest income together instead of splitting them by sign).
_SORT_COLUMNS: dict[str, str] = {
    "date":     "date",
    "amount":   "ABS(amount)",
    "vendor":   "LOWER(COALESCE(vendor, description))",
    "category": "COALESCE(category, '')",
}


@app.get("/transactions", response_class=HTMLResponse)
def transactions_page(
    request: Request,
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None, alias="to"),
    category: str = Query(""),
    q: str = Query(""),
    profile: str | None = Query(None),
    kind: str = Query(""),  # "" | "spend" | "income"
    sort: str = Query("date"),
    dir: str = Query("desc"),
    page: int = Query(1, ge=1),
):
    """
    Browse all transactions with filters. Default range is all-time (unlike
    the dashboard) because people come here to look up old transactions.
    """
    earliest = _earliest_transaction_date()
    start, end, range_label = _resolve_range(from_, to, default="all", earliest=earliest)
    start_s, end_s = start.isoformat(), end.isoformat()
    profile_id, profile_label = _resolve_profile(profile)

    # Resolve sort — fall back to the default rather than 400ing on junk input.
    if sort not in _SORT_COLUMNS:
        sort = "date"
    direction = "ASC" if dir.lower() == "asc" else "DESC"
    # Always tiebreak on id DESC so pagination is stable when many rows share
    # the sort value (e.g. same-day transactions, or identical amounts).
    order_sql = f"{_SORT_COLUMNS[sort]} {direction}, id DESC"

    where = ["date BETWEEN ? AND ?"]
    params: list = [start_s, end_s]
    if category:
        where.append("COALESCE(category, '') = ?")
        params.append(category)
    if q:
        where.append("(LOWER(COALESCE(description, '')) LIKE ? OR LOWER(COALESCE(vendor, '')) LIKE ?)")
        needle = f"%{q.lower()}%"
        params += [needle, needle]
    if profile_id is not None:
        where.append("profile_id = ?")
        params.append(profile_id)
    # Kind filter: Income (amount > 0) / Spent (amount < 0). Junk values fall
    # through to "All" rather than 400ing, matching how sort/dir behave.
    if kind == "spend":
        where.append("amount < 0")
    elif kind == "income":
        where.append("amount > 0")
    else:
        kind = ""
    where_sql = " AND ".join(where)

    offset = (page - 1) * PAGE_SIZE
    with connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM transactions WHERE {where_sql}",
            params,
        ).fetchone()["n"]

        rows = conn.execute(
            f"""SELECT id, date, amount, currency, vendor, description, category
                FROM transactions
                WHERE {where_sql}
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?""",
            params + [PAGE_SIZE, offset],
        ).fetchall()

        sums = conn.execute(
            f"""SELECT
                    COALESCE(SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END), 0) AS spent,
                    COALESCE(SUM(CASE WHEN amount > 0 THEN  amount ELSE 0 END), 0) AS income
                FROM transactions
                WHERE {where_sql} AND COALESCE(category, '') != 'Transfers'""",
            params,
        ).fetchone()

    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    categories = _load_categories()
    profiles = _load_profiles()
    presets = _build_presets(date.today(), earliest=earliest)

    return templates.TemplateResponse(
        request,
        "transactions.html",
        {
            "range_label": range_label,
            "range_from": start_s,
            "range_to": end_s,
            "range_min": earliest.isoformat(),
            "is_default_range": not from_ and not to,
            "presets": presets,
            "category": category,
            "q": q,
            "profile_id": profile_id,
            "profile_label": profile_label,
            "profiles": profiles,
            "kind": kind,
            "sort": sort,
            "dir": direction.lower(),
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "rows": [dict(r) for r in rows],
            "sums": dict(sums),
            "categories": categories,
            "suggest_pattern": suggest_pattern,
        },
    )


@app.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request):
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, pattern, category, vendor, priority FROM rules ORDER BY category, pattern"
        ).fetchall()
    categories = _load_categories()
    return templates.TemplateResponse(
        request,
        "rules.html",
        {"rules": [dict(r) for r in rows], "categories": categories},
    )


@app.get("/rules/{rule_id}/edit", response_class=HTMLResponse)
def rule_edit_row(request: Request, rule_id: int):
    """Return the edit-mode row HTML for htmx to swap in."""
    with connect() as conn:
        row = conn.execute(
            "SELECT id, pattern, category, vendor, priority FROM rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="rule not found")
    categories = _load_categories()
    return templates.TemplateResponse(
        request,
        "_rule_row.html",
        {"r": dict(row), "categories": categories, "edit": True},
    )


@app.get("/rules/{rule_id}/row", response_class=HTMLResponse)
def rule_view_row(request: Request, rule_id: int):
    """Return the display-mode row HTML — used by the Cancel button in edit."""
    with connect() as conn:
        row = conn.execute(
            "SELECT id, pattern, category, vendor, priority FROM rules WHERE id = ?",
            (rule_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="rule not found")
    return templates.TemplateResponse(
        request, "_rule_row.html", {"r": dict(row), "edit": False},
    )


@app.post("/rules/{rule_id}", response_class=HTMLResponse)
def update_rule(
    request: Request,
    rule_id: int,
    pattern: str = Form(...),
    category: str = Form(...),
    category_new: str = Form(""),
    vendor: str = Form(""),
    priority: int = Form(100),
):
    from .rules import update_rule_and_recategorize
    with connect() as conn:
        resolved_category = _resolve_category(conn, category, category_new)
        if not resolved_category:
            raise HTTPException(status_code=400, detail="category name required")
        summary = update_rule_and_recategorize(
            conn,
            rule_id=rule_id,
            pattern=pattern.strip(),
            category=resolved_category,
            vendor=vendor.strip() or None,
            priority=priority,
        )
        row = conn.execute(
            "SELECT id, pattern, category, vendor, priority FROM rules WHERE id = ?",
            (rule_id,),
        ).fetchone()

    return templates.TemplateResponse(
        request,
        "_rule_row.html",
        {
            "r": dict(row),
            "edit": False,
            "flash_count": summary["updated"],
        },
    )


@app.post("/rules/{rule_id}/delete")
def delete_rule(rule_id: int):
    with connect() as conn:
        conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    return RedirectResponse(url="/rules", status_code=303)


@app.get("/profiles", response_class=HTMLResponse)
def profiles_page(request: Request, error: str | None = None):
    from .db import DEFAULT_PROFILE_NAME
    with connect() as conn:
        rows = conn.execute(
            """SELECT p.id, p.name, p.color,
                      (SELECT COUNT(*) FROM transactions t WHERE t.profile_id = p.id) AS tx_count
               FROM profiles p
               ORDER BY p.id"""
        ).fetchall()
    return templates.TemplateResponse(
        request,
        "profiles.html",
        {
            "profiles": [dict(r) for r in rows],
            "default_name": DEFAULT_PROFILE_NAME,
            "error": error,
        },
    )


@app.post("/profiles")
def create_profile(name: str = Form(...), color: str = Form("#94a3b8")):
    name = name.strip()
    if not name:
        return RedirectResponse(url="/profiles?error=Name+cannot+be+empty", status_code=303)
    with connect() as conn:
        existing = conn.execute("SELECT 1 FROM profiles WHERE name = ?", (name,)).fetchone()
        if existing:
            return RedirectResponse(url="/profiles?error=Profile+already+exists", status_code=303)
        conn.execute("INSERT INTO profiles (name, color) VALUES (?, ?)", (name, color))
    return RedirectResponse(url="/profiles", status_code=303)


@app.post("/profiles/{profile_id}/delete")
def delete_profile(profile_id: int):
    # Refuse to delete the default profile — it's the fallback we stamp onto
    # imports when no profile is picked, so something has to keep that name.
    from .db import DEFAULT_PROFILE_NAME
    with connect() as conn:
        row = conn.execute("SELECT name FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        if row is None:
            return RedirectResponse(url="/profiles", status_code=303)
        if row["name"] == DEFAULT_PROFILE_NAME:
            return RedirectResponse(
                url="/profiles?error=Cannot+delete+the+default+profile",
                status_code=303,
            )
        # ON DELETE SET NULL on the FK leaves transactions orphaned, which the
        # dashboard filter then shows in the combined view only. Re-stamp to
        # the default profile instead so they stay visible on a per-profile view.
        default = conn.execute(
            "SELECT id FROM profiles WHERE name = ?", (DEFAULT_PROFILE_NAME,)
        ).fetchone()
        conn.execute(
            "UPDATE transactions SET profile_id = ? WHERE profile_id = ?",
            (default["id"], profile_id),
        )
        conn.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
    return RedirectResponse(url="/profiles", status_code=303)
