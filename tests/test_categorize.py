"""
Smoke tests against real vendor strings seen in Pekao exports, so we know the
default rules actually cover the common cases.
"""
import os
import tempfile
from datetime import date
from pathlib import Path

import pytest

# Use a fresh temp DB so we don't touch the user's real one.
@pytest.fixture
def tmp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("MONEYPIT_DB", path)
    # Reload the module so it picks up the new env var.
    import importlib
    import moneypit.db as db
    importlib.reload(db)
    db.init_db()
    yield db
    Path(path).unlink(missing_ok=True)


def _categorize(description: str, tmp_db, amount=-100.0, op_type="card"):
    from moneypit.categorize import apply_rules
    from moneypit.models import Transaction
    tx = Transaction(
        date=date(2026, 4, 1),
        amount=amount,
        currency="PLN",
        description=description,
        vendor=description,
        op_type=op_type,
    )
    with tmp_db.connect() as conn:
        apply_rules(tx, conn)
    return tx.category, tx.vendor


# Real vendor strings from a Pekao export (sanitized, no accounts/IDs).
REAL_PEKAO_VENDORS = [
    ("JMP S.A. BIEDRO        TORUN",            "Groceries",        "Biedronka"),
    ("PIEKARNIA PIEKU        TORUN",            "Groceries",        "Piekarnia"),
    ("PIEKARNIA BARTK        TORUN",            "Groceries",        "Piekarnia"),
    ("TERG SPOLKA AKCYJNA",                     "Shopping",         "Media Expert"),
    ("ALIEXPRESS.COM Luxembourg",               "Shopping",         "AliExpress"),
    ("ZALANDO PAYMENT        BERLIN",           "Shopping",         "Zalando"),
    ("ZALANDO PAYMENTS       BERLIN",           "Shopping",         "Zalando"),
    ("LPP HOUSE 31121        TORUN",            "Shopping",         None),
    ("SPORTSDIRECT CO        TORUN",            "Shopping",         "SportsDirect"),
    ("GUESS EUROPE SAGL",                       "Shopping",         "Guess"),
    ("EMPIK.COM              WARSZAWA",         "Shopping",         "Empik"),
    ("WHALECO TECHNOLOGY LTD",                  "Shopping",         "Temu"),
    ("DISPLATE COM           MAZOWIECKIE",      "Subscriptions",    "Displate"),
    ("CANAL+ POLSKA S        WARSZAWA",         "Subscriptions",    "Canal+"),
    ("APPLE.COM/BILL         APPLE.COM/BI",     "Subscriptions",    "Apple"),
    ("P4 SP Z OO             WARSZAWA",         "Bills & Utilities", "Play"),
    ("ENERGA24               GDANSK",           "Bills & Utilities", "Energa"),
    ("PZU NA ŻYCIE SA",                         "Health",           "PZU"),
    ("MYJNIA AWIX OIL        TORUN",            "Transport",        "Car Wash"),
    ("SKYCASH.COM            WARSZAWA",         "Transport",        "SkyCash"),
    ("SPŁATA KREDYTU",                          "Transfers",        None),
]


@pytest.mark.parametrize("description,expected_category,expected_vendor", REAL_PEKAO_VENDORS)
def test_real_pekao_vendors(description, expected_category, expected_vendor, tmp_db):
    category, vendor = _categorize(description, tmp_db)
    assert category == expected_category, (
        f"{description!r} → expected {expected_category}, got {category}"
    )
    if expected_vendor is not None:
        assert vendor == expected_vendor, (
            f"{description!r} → expected vendor {expected_vendor}, got {vendor}"
        )


def test_income_default(tmp_db):
    # Unknown positive amounts get classified as Income (not Uncategorized)
    category, _ = _categorize("SOME UNKNOWN EMPLOYER", tmp_db, amount=5000.0, op_type="transfer")
    # Salary via transfer → categorized as Transfers unless we match... let's
    # just check the default-case income path with a non-transfer.
    category2, _ = _categorize("UNKNOWN SOURCE", tmp_db, amount=5000.0, op_type="other")
    assert category2 == "Income"


def test_diacritic_insensitive(tmp_db):
    # Both the diacritic and stripped forms should match the same rule
    c1, _ = _categorize("ZABKA NANO 123", tmp_db)
    c2, _ = _categorize("ŻABKA NANO 123", tmp_db)
    assert c1 == c2 == "Groceries"
