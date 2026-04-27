"""
Helpers for teaching the categorizer new rules from the UI.
"""
import re
import sqlite3

from .categorize import _normalize


# Words we strip from a suggested pattern because they're noise, not identity:
# Polish city names, legal-entity suffixes, payment-processor markers, etc.
_NOISE_TOKENS = {
    # Polish legal entity suffixes
    "sp", "z", "o", "oo", "o.o", "o.o.", "sp.z", "spolka", "spółka", "akcyjna",
    "s.a", "s.a.", "sa", "ltd", "limited", "ag", "gmbh", "inc", "corp",
    # Cities (Polish)
    "warszawa", "krakow", "kraków", "wroclaw", "wrocław", "poznan", "poznań",
    "gdansk", "gdańsk", "lodz", "łódź", "szczecin", "bydgoszcz", "lublin",
    "katowice", "bialystok", "białystok", "torun", "toruń", "gdynia",
    "czestochowa", "częstochowa", "radom", "sosnowiec", "kielce", "gliwice",
    "olsztyn", "rzeszow", "rzeszów", "zabrze", "tychy", "opole", "bytom",
    "berlin", "luxembourg", "london", "mazowieckie", "malopolskie",
    # Payment-processor noise
    "payment", "payments", "ref", "blik", "com",
}


def suggest_pattern(description: str) -> str:
    """
    Turn a raw transaction description into a reasonable rule pattern.

    Strategy:
    1. Normalize (lowercase + strip diacritics) so the rule matches variants.
    2. Drop tokens that are pure noise (city names, legal suffixes).
    3. Keep the first 2-3 meaningful tokens — enough to identify the merchant
       without being so specific that it won't match the next charge from
       the same place.

    Examples:
        "JMP S.A. BIEDRO        TORUN"     -> "jmp biedro"
        "ZALANDO PAYMENT        BERLIN"    -> "zalando"
        "P4 SP Z OO             WARSZAWA"  -> "p4"
        "APPLE.COM/BILL         APPLE.COM/BI" -> "apple.com/bill"
    """
    norm = _normalize(description)
    # Split on whitespace and common separators but keep dots/slashes (for URLs)
    tokens = re.split(r"[\s,;]+", norm)
    tokens = [t for t in tokens if t and t not in _NOISE_TOKENS]

    # Drop tokens that are pure punctuation/digits (account fragments etc.)
    tokens = [t for t in tokens if not re.fullmatch(r"[\d\W_]+", t)]

    # Dedupe adjacent + near-duplicate tokens. Pekao repeats vendor names:
    # "APPLE.COM/BILL  APPLE.COM/BI" -> keep the first, drop the second.
    deduped: list[str] = []
    for t in tokens:
        if any(t == d or t.startswith(d) or d.startswith(t) for d in deduped):
            continue
        deduped.append(t)
    tokens = deduped

    if not tokens:
        # Fall back to the raw normalized string if our filters ate everything.
        return norm.strip()[:40]

    # Two tokens is usually the sweet spot: "jmp biedro" beats "jmp" (too
    # generic, matches JMP Records too) and beats "jmp s.a biedro torun"
    # (won't match the next Biedronka branch).
    return " ".join(tokens[:2])


def create_rule_and_recategorize(
    conn: sqlite3.Connection,
    pattern: str,
    category: str,
    vendor: str | None = None,
    user_id: int | None = None,
) -> dict:
    """
    Insert a new rule and apply it retroactively to matching transactions.
    When user_id is provided, only recategorizes that user's transactions.
    """
    pattern = pattern.strip()
    if not pattern:
        raise ValueError("pattern cannot be empty")

    cur = conn.execute(
        "INSERT INTO rules (pattern, category, vendor, priority) VALUES (?, ?, ?, 50)",
        (pattern, category, vendor or None),
    )
    rule_id = cur.lastrowid

    needle = _normalize(pattern)
    if user_id is not None:
        rows = conn.execute(
            """SELECT t.id, t.description, t.vendor FROM transactions t
               JOIN profiles p ON p.id = t.profile_id
               WHERE p.user_id = ?""",
            (user_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, description, vendor FROM transactions"
        ).fetchall()

    updated = 0
    for row in rows:
        haystack = _normalize(f"{row['description'] or ''} {row['vendor'] or ''}")
        if needle in haystack:
            conn.execute(
                "UPDATE transactions SET category = ?, vendor = COALESCE(?, vendor) WHERE id = ?",
                (category, vendor, row["id"]),
            )
            updated += 1

    return {"rule_id": rule_id, "updated": updated}


def update_rule_and_recategorize(
    conn: sqlite3.Connection,
    rule_id: int,
    pattern: str,
    category: str,
    vendor: str | None = None,
    priority: int | None = None,
    user_id: int | None = None,
) -> dict:
    """
    Update an existing rule and re-apply categorization retroactively.

    Two-pass fix so a rename doesn't strand rows:
      1. Find transactions likely tagged by the *old* rule — i.e. those whose
         text matched the old pattern AND whose current category is the old
         category. Clear them and re-run `apply_rules` so the next matching
         rule (or "Uncategorized") takes over.
      2. Apply the new pattern/category to any transaction now matching it.

    Returns the number of transactions whose category or vendor changed.
    """
    from .categorize import apply_rules
    from .models import Transaction
    from datetime import date as date_cls

    pattern = pattern.strip()
    if not pattern:
        raise ValueError("pattern cannot be empty")

    old = conn.execute(
        "SELECT pattern, category, vendor FROM rules WHERE id = ?", (rule_id,)
    ).fetchone()
    if old is None:
        raise ValueError(f"rule {rule_id} not found")

    old_needle = _normalize(old["pattern"])
    old_category = old["category"]

    update_sql = "UPDATE rules SET pattern = ?, category = ?, vendor = ?"
    params: list = [pattern, category, vendor or None]
    if priority is not None:
        update_sql += ", priority = ?"
        params.append(priority)
    update_sql += " WHERE id = ?"
    params.append(rule_id)
    conn.execute(update_sql, params)

    if user_id is not None:
        all_rows = conn.execute(
            "SELECT t.id, t.date, t.amount, t.currency, t.description, t.vendor, t.category, t.op_type "
            "FROM transactions t JOIN profiles p ON p.id = t.profile_id "
            "WHERE p.user_id = ?",
            (user_id,),
        ).fetchall()
    else:
        all_rows = conn.execute(
            "SELECT id, date, amount, currency, description, vendor, category, op_type "
            "FROM transactions"
        ).fetchall()

    changed = 0
    new_needle = _normalize(pattern)

    for row in all_rows:
        haystack = _normalize(f"{row['description'] or ''} {row['vendor'] or ''}")
        orig_category = row["category"]
        orig_vendor = row["vendor"]

        was_tagged_by_old = (old_needle in haystack) and (row["category"] == old_category)
        matches_new = new_needle in haystack

        if matches_new:
            new_vendor = vendor or row["vendor"]
            if row["category"] != category or new_vendor != row["vendor"]:
                conn.execute(
                    "UPDATE transactions SET category = ?, vendor = COALESCE(?, vendor) WHERE id = ?",
                    (category, vendor, row["id"]),
                )
                changed += 1
        elif was_tagged_by_old:
            # Old pattern matched and old category was applied, but new pattern
            # doesn't match — rerun the rules engine to find a replacement.
            # Instantiate a bare Transaction so apply_rules can fill category.
            tx = Transaction(
                date=date_cls.fromisoformat(row["date"]),
                amount=row["amount"],
                currency=row["currency"] if "currency" in row.keys() else "PLN",
                description=row["description"] or "",
                vendor=row["vendor"],
                category=None,
                op_type=row["op_type"],
            )
            apply_rules(tx, conn)
            new_category = tx.category or "Uncategorized"
            if new_category != orig_category or tx.vendor != orig_vendor:
                conn.execute(
                    "UPDATE transactions SET category = ?, vendor = ? WHERE id = ?",
                    (new_category, tx.vendor, row["id"]),
                )
                changed += 1

    return {"rule_id": rule_id, "updated": changed}
