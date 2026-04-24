from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class Transaction:
    date: date
    amount: float                # negative = spend
    currency: str
    description: str             # raw text from the bank
    vendor: Optional[str] = None
    category: Optional[str] = None
    op_type: Optional[str] = None
    source: str = "csv"
    source_bank: Optional[str] = None
    source_ref: Optional[str] = None
    profile_id: Optional[int] = None

    def hash_key(self) -> str:
        """Stable dedup key. Same transaction re-imported = same hash.

        `profile_id` is part of the hash so two profiles can each import an
        identical-looking charge (e.g. both partners bought groceries at the
        same Biedronka for the same price on the same day) without the
        second import being silently skipped as a duplicate.
        """
        import hashlib
        raw = f"{self.profile_id or 0}|{self.date.isoformat()}|{self.amount:.2f}|{self.currency}|{self.description.strip().lower()}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()
