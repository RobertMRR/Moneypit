"""
Rules-based categorization. Later we can layer an LLM fallback on top for
unknown vendors, but v0.1 stays rules-only.
"""
import sqlite3
import unicodedata

from .models import Transaction


def _normalize(s: str) -> str:
    """
    Lowercase and strip diacritics so 'Żabka', 'ZABKA', 'zabka' all match the
    same rule. Polish bank exports are inconsistent about diacritics
    (sometimes present, sometimes stripped, sometimes mojibake).
    """
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def apply_rules(tx: Transaction, conn: sqlite3.Connection) -> None:
    """Mutate tx in place: fill category and (optionally) canonical vendor."""
    if tx.category:
        return

    haystack = _normalize(f"{tx.description or ''} {tx.vendor or ''}")

    rows = conn.execute(
        "SELECT pattern, category, vendor FROM rules ORDER BY priority ASC, id ASC"
    ).fetchall()
    for row in rows:
        pattern = _normalize(row["pattern"])
        if pattern in haystack:
            tx.category = row["category"]
            if row["vendor"]:
                tx.vendor = row["vendor"]
            return

    if tx.amount > 0 and tx.op_type != "transfer":
        tx.category = "Income"
    else:
        tx.category = "Uncategorized"
