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
) -> dict:
    """
    Insert a new rule and apply it retroactively to all transactions whose
    (description + vendor) contains the pattern. Returns a summary.
    """
    pattern = pattern.strip()
    if not pattern:
        raise ValueError("pattern cannot be empty")

    cur = conn.execute(
        "INSERT INTO rules (pattern, category, vendor, priority) VALUES (?, ?, ?, 50)",
        (pattern, category, vendor or None),
    )
    rule_id = cur.lastrowid

    # Retroactively categorize matching transactions. We can't use LIKE with
    # normalized strings, so we fetch candidates and filter in Python. This
    # is fine — it runs once per rule creation, not per request.
    needle = _normalize(pattern)
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
