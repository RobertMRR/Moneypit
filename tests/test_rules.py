import os
import tempfile
from datetime import date
from pathlib import Path

import pytest

from moneypit.rules import suggest_pattern


@pytest.mark.parametrize("description,expected", [
    ("JMP S.A. BIEDRO        TORUN",         "jmp biedro"),
    ("ZALANDO PAYMENT        BERLIN",        "zalando"),
    ("P4 SP Z OO             WARSZAWA",      "p4"),
    ("APPLE.COM/BILL         APPLE.COM/BI",  "apple.com/bill"),
    ("ALIEXPRESS.COM Luxembourg",            "aliexpress.com"),
    ("SKYCASH.COM            WARSZAWA",      "skycash.com"),
    ("PZU NA ŻYCIE SA",                      "pzu na"),
])
def test_suggest_pattern(description, expected):
    assert suggest_pattern(description) == expected


def test_suggest_pattern_falls_back_on_all_noise():
    # Pure noise tokens shouldn't produce an empty pattern.
    result = suggest_pattern("SP Z O.O. WARSZAWA")
    assert result  # non-empty


@pytest.fixture
def tmp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("MONEYPIT_DB", path)
    import importlib
    import moneypit.db as db
    importlib.reload(db)
    db.init_db()
    yield db
    Path(path).unlink(missing_ok=True)


def test_create_rule_retroactively_categorizes(tmp_db):
    from moneypit.rules import create_rule_and_recategorize

    with tmp_db.connect() as conn:
        # Insert three transactions: two matching, one not.
        for i, (desc, vendor) in enumerate([
            ("MYJNIA AWIX OIL        TORUN", "MYJNIA AWIX OIL        TORUN"),
            ("MYJNIA AWIX OIL        TORUN", "MYJNIA AWIX OIL        TORUN"),
            ("BIEDRONKA",                    "BIEDRONKA"),
        ]):
            conn.execute(
                """INSERT INTO transactions
                   (date, amount, currency, description, vendor, category,
                    source, hash)
                   VALUES (?, ?, ?, ?, ?, 'Uncategorized', 'csv', ?)""",
                ("2026-04-01", -10.0, "PLN", desc, vendor, f"h{i}"),
            )

        summary = create_rule_and_recategorize(
            conn, pattern="myjnia awix", category="Transport", vendor="Car Wash"
        )

        assert summary["updated"] == 2

        categorized = conn.execute(
            "SELECT vendor, category FROM transactions WHERE description LIKE 'MYJNIA%'"
        ).fetchall()
        for row in categorized:
            assert row["category"] == "Transport"
            assert row["vendor"] == "Car Wash"

        # The unrelated row was not touched.
        other = conn.execute(
            "SELECT category FROM transactions WHERE description = 'BIEDRONKA'"
        ).fetchone()
        assert other["category"] == "Uncategorized"

        # The rule was actually persisted.
        rule = conn.execute(
            "SELECT pattern, category FROM rules WHERE id = ?", (summary["rule_id"],)
        ).fetchone()
        assert rule["pattern"] == "myjnia awix"
        assert rule["category"] == "Transport"


def test_create_rule_rejects_empty_pattern(tmp_db):
    from moneypit.rules import create_rule_and_recategorize

    with tmp_db.connect() as conn:
        with pytest.raises(ValueError):
            create_rule_and_recategorize(conn, pattern="   ", category="Shopping")
