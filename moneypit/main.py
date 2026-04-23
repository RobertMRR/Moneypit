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


DEFAULT_ALL_TIME_START = date(1970, 1, 1)


def _resolve_range(
    from_: str | None,
    to: str | None,
    default: str = "last30",
) -> tuple[date, date, str]:
    """
    Parse ?from= / ?to= query params into concrete dates. When both are
    missing, falls back to `default`:
      - "last30"  -> last 30 days ending today (dashboard default)
      - "all"     -> all time (transactions browser default)
    Returns (start, end, human_label).
    """
    today = date.today()
    try:
        end = date.fromisoformat(to) if to else today
    except ValueError:
        end = today
    try:
        if from_:
            start = date.fromisoformat(from_)
        elif default == "all":
            start = DEFAULT_ALL_TIME_START
        else:
            start = end - timedelta(days=29)
    except ValueError:
        start = end - timedelta(days=29) if default != "all" else DEFAULT_ALL_TIME_START

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


def _build_presets(today: date) -> list[tuple[str, str, str]]:
    """Quick-pick date ranges shared by dashboard and transactions pages."""
    first_of_this_month = today.replace(day=1)
    last_of_prev_month = first_of_this_month - timedelta(days=1)
    first_of_prev_month = last_of_prev_month.replace(day=1)
    return [
        ("Last 30 days", "",                                 ""),
        ("This month",   first_of_this_month.isoformat(),    today.isoformat()),
        ("Last month",   first_of_prev_month.isoformat(),    last_of_prev_month.isoformat()),
        ("This year",    date(today.year, 1, 1).isoformat(), today.isoformat()),
        ("All time",     DEFAULT_ALL_TIME_START.isoformat(), today.isoformat()),
    ]


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None, alias="to"),
):
    start, end, range_label = _resolve_range(from_, to)
    start_s, end_s = start.isoformat(), end.isoformat()

    with connect() as conn:
        by_category = conn.execute(
            """SELECT category, SUM(-amount) AS total, COUNT(*) AS n
               FROM transactions
               WHERE amount < 0 AND date BETWEEN ? AND ?
               GROUP BY category
               ORDER BY total DESC""",
            (start_s, end_s),
        ).fetchall()

        top_vendors = conn.execute(
            """SELECT vendor, SUM(-amount) AS total, COUNT(*) AS n
               FROM transactions
               WHERE amount < 0 AND date BETWEEN ? AND ? AND vendor IS NOT NULL
               GROUP BY vendor
               ORDER BY total DESC
               LIMIT 10""",
            (start_s, end_s),
        ).fetchall()

        recent = conn.execute(
            """SELECT id, date, amount, currency, vendor, description, category
               FROM transactions
               WHERE date BETWEEN ? AND ?
               ORDER BY date DESC, id DESC
               LIMIT 25""",
            (start_s, end_s),
        ).fetchall()

        # Uncategorized intentionally ignores the date filter — if you have
        # an uncategorized transaction from 3 months ago, you still want
        # the chance to fix it without juggling date pickers.
        uncategorized = conn.execute(
            """SELECT id, date, amount, currency, vendor, description, category
               FROM transactions
               WHERE category = 'Uncategorized'
               ORDER BY ABS(amount) DESC, date DESC
               LIMIT 25"""
        ).fetchall()

        # Exclude Transfers from income/spend totals — moving money between
        # your own accounts or repaying a credit card isn't real income or
        # real spending, and counting it double-inflates both sides.
        totals = conn.execute(
            """SELECT
                   COALESCE(SUM(CASE WHEN amount < 0 THEN -amount ELSE 0 END), 0) AS spent,
                   COALESCE(SUM(CASE WHEN amount > 0 THEN  amount ELSE 0 END), 0) AS income
               FROM transactions
               WHERE date BETWEEN ? AND ? AND COALESCE(category, '') != 'Transfers'""",
            (start_s, end_s),
        ).fetchone()
        total_spend = totals["spent"]
        total_income = totals["income"]
        net = total_income - total_spend

    recurring = detect_recurring()  # all-time, intentionally
    categories = _load_categories()
    presets = _build_presets(date.today())

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "range_label": range_label,
            "range_from": start_s,
            "range_to": end_s,
            "is_default_range": not from_ and not to,
            "presets": presets,
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
    return templates.TemplateResponse(
        request,
        "import.html",
        {
            "categories": categories,
            "imported": imported,
            "skipped": skipped,
            "bank": bank,
            "added": added,
            "error": error,
            "today": date.today().isoformat(),
        },
    )


@app.post("/import/csv")
async def import_csv(file: UploadFile = File(...)):
    content = await file.read()
    try:
        bank, txs = parse_csv(content, source_ref=file.filename or "upload")
    except ValueError as e:
        return RedirectResponse(url=f"/import?error={e}", status_code=303)

    inserted = skipped = 0
    with connect() as conn:
        for tx in txs:
            apply_rules(tx, conn)
            try:
                conn.execute(
                    """INSERT INTO transactions
                       (date, amount, currency, description, vendor, category,
                        op_type, source, source_bank, source_ref, hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        tx.date.isoformat(), tx.amount, tx.currency,
                        tx.description, tx.vendor, tx.category,
                        tx.op_type, tx.source, tx.source_bank, tx.source_ref,
                        tx.hash_key(),
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


@app.post("/transactions")
def add_transaction(
    tx_date: str = Form(..., alias="date"),
    amount: float = Form(...),
    direction: str = Form("spend"),  # "spend" | "income"
    currency: str = Form("PLN"),
    vendor: str = Form(""),
    description: str = Form(""),
    category: str = Form(""),
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
    tx = TxModel(
        date=parsed_date,
        amount=signed,
        currency=currency.strip().upper() or "PLN",
        description=desc,
        vendor=vendor.strip() or None,
        category=category.strip() or None,
        source="manual",
    )

    # Manual entries need a salted hash so that honest duplicates (two 15 PLN
    # coffees at the same place on the same day) don't collide with the
    # UNIQUE constraint. CSV imports rely on the deterministic hash for
    # idempotent re-imports, but manual submits are one-shot by nature.
    salted = f"manual|{secrets.token_hex(8)}|{tx.hash_key()}"

    with connect() as conn:
        if not tx.category:
            apply_rules(tx, conn)
        conn.execute(
            """INSERT INTO transactions
               (date, amount, currency, description, vendor, category,
                op_type, source, source_bank, source_ref, hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tx.date.isoformat(), tx.amount, tx.currency,
                tx.description, tx.vendor, tx.category,
                tx.op_type, tx.source, None, None, salted,
            ),
        )

    return RedirectResponse(url="/import?added=1", status_code=303)


@app.post("/transactions/{tx_id}/categorize", response_class=HTMLResponse)
def categorize_transaction(
    request: Request,
    tx_id: int,
    category: str = Form(...),
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

        updated_count = 1
        if create_rule and pattern.strip():
            summary = create_rule_and_recategorize(
                conn, pattern.strip(), category, vendor.strip() or None
            )
            updated_count = summary["updated"]
        else:
            conn.execute(
                "UPDATE transactions SET category = ?, vendor = COALESCE(NULLIF(?, ''), vendor) WHERE id = ?",
                (category, vendor.strip(), tx_id),
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


@app.get("/transactions", response_class=HTMLResponse)
def transactions_page(
    request: Request,
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None, alias="to"),
    category: str = Query(""),
    q: str = Query(""),
    page: int = Query(1, ge=1),
):
    """
    Browse all transactions with filters. Default range is all-time (unlike
    the dashboard) because people come here to look up old transactions.
    """
    start, end, range_label = _resolve_range(from_, to, default="all")
    start_s, end_s = start.isoformat(), end.isoformat()

    where = ["date BETWEEN ? AND ?"]
    params: list = [start_s, end_s]
    if category:
        where.append("COALESCE(category, '') = ?")
        params.append(category)
    if q:
        where.append("(LOWER(COALESCE(description, '')) LIKE ? OR LOWER(COALESCE(vendor, '')) LIKE ?)")
        needle = f"%{q.lower()}%"
        params += [needle, needle]
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
                ORDER BY date DESC, id DESC
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
    presets = _build_presets(date.today())

    return templates.TemplateResponse(
        request,
        "transactions.html",
        {
            "range_label": range_label,
            "range_from": start_s,
            "range_to": end_s,
            "is_default_range": not from_ and not to,
            "presets": presets,
            "category": category,
            "q": q,
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
    return templates.TemplateResponse(
        request,
        "rules.html",
        {"rules": [dict(r) for r in rows]},
    )


@app.post("/rules/{rule_id}/delete")
def delete_rule(rule_id: int):
    with connect() as conn:
        conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    return RedirectResponse(url="/rules", status_code=303)
