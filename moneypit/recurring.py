"""
Detect recurring charges (subscriptions) from transaction history.

Heuristic: same vendor, similar amount (within 10%), charged 2+ times with
gaps that look monthly (25-35 days) or yearly (350-380 days).
"""
from collections import defaultdict
from datetime import date, datetime
from typing import Literal

from .db import connect

Cadence = Literal["monthly", "yearly", "irregular"]


def detect_recurring() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT date, amount, vendor, description, category
               FROM transactions
               WHERE amount < 0 AND vendor IS NOT NULL
               ORDER BY vendor, date"""
        ).fetchall()

    by_vendor: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_vendor[r["vendor"]].append(dict(r))

    recurring = []
    for vendor, charges in by_vendor.items():
        if len(charges) < 2:
            continue

        gaps = []
        prev = datetime.fromisoformat(charges[0]["date"]).date()
        for c in charges[1:]:
            cur = datetime.fromisoformat(c["date"]).date()
            gaps.append((cur - prev).days)
            prev = cur

        cadence = _classify_cadence(gaps)
        if cadence == "irregular":
            continue

        amounts = [abs(c["amount"]) for c in charges]
        avg = sum(amounts) / len(amounts)
        spread = (max(amounts) - min(amounts)) / avg if avg else 1.0
        if spread > 0.2:  # too variable to be a real subscription
            continue

        recurring.append({
            "vendor": vendor,
            "category": charges[-1]["category"],
            "cadence": cadence,
            "avg_amount": round(avg, 2),
            "last_charged": charges[-1]["date"],
            "count": len(charges),
        })

    recurring.sort(key=lambda r: r["avg_amount"], reverse=True)
    return recurring


def _classify_cadence(gaps: list[int]) -> Cadence:
    if not gaps:
        return "irregular"
    monthly = sum(1 for g in gaps if 25 <= g <= 35)
    yearly = sum(1 for g in gaps if 350 <= g <= 380)
    if monthly >= max(1, len(gaps) // 2):
        return "monthly"
    if yearly >= 1 and len(gaps) <= 3:
        return "yearly"
    return "irregular"
