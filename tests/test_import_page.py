"""
Tests for /import page and manual transaction entry.
"""
import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def client(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("MONEYPIT_DB", path)

    import importlib
    import moneypit.db as db
    importlib.reload(db)
    db.init_db()

    from tests.conftest import create_test_user
    cookies = create_test_user(db)

    import moneypit.main
    importlib.reload(moneypit.main)
    from fastapi.testclient import TestClient
    yield TestClient(moneypit.main.app, cookies=cookies)
    Path(path).unlink(missing_ok=True)


def test_import_page_renders(client):
    r = client.get("/import")
    assert r.status_code == 200
    assert "Import bank CSV" in r.text
    assert "Add transaction manually" in r.text


def test_import_page_has_nav_link(client):
    r = client.get("/")
    assert "/import" in r.text
    assert ">Import<" in r.text


def test_csv_upload_redirects_with_summary(client):
    pekao_csv = (
        "Data księgowania;Data waluty;Nadawca / Odbiorca;Adres nadawcy / odbiorcy;"
        "Rachunek źródłowy;Rachunek docelowy;Tytułem;Kwota operacji;Waluta;"
        "Numer referencyjny;Typ operacji;Kategoria\n"
        "23.04.2026;23.04.2026;BIEDRONKA 123;;;;;-50,00;PLN;;TRANSAKCJA KARTĄ PŁATNICZĄ;Bez kategorii\n"
    ).encode("utf-8")

    r = client.post(
        "/import/csv",
        files={"file": ("test.csv", pekao_csv, "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "imported=1" in r.headers["location"]
    assert "bank=pekao" in r.headers["location"]


def test_csv_upload_bad_format_shows_error(client):
    r = client.post(
        "/import/csv",
        files={"file": ("junk.csv", b"not,a,real,csv\n1,2,3,4\n", "text/csv")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=" in r.headers["location"]


def test_manual_expense(client):
    r = client.post(
        "/transactions",
        data={
            "date": "2026-04-20",
            "amount": "42.50",
            "direction": "spend",
            "currency": "PLN",
            "vendor": "Coffee Shop",
            "description": "Cappuccino",
            "category": "Eating out",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "added=1" in r.headers["location"]

    # Verify it's in the DB as a negative amount
    import moneypit.db as db
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM transactions WHERE vendor='Coffee Shop'").fetchone()
    assert row is not None
    assert row["amount"] == -42.50
    assert row["source"] == "manual"
    assert row["category"] == "Eating out"


def test_manual_income(client):
    r = client.post(
        "/transactions",
        data={
            "date": "2026-04-20",
            "amount": "300.00",
            "direction": "income",
            "currency": "PLN",
            "vendor": "Side gig",
            "category": "Income",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    import moneypit.db as db
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM transactions WHERE vendor='Side gig'").fetchone()
    assert row["amount"] == 300.00  # positive


def test_manual_duplicates_both_kept(client):
    # Two identical manual entries should NOT be deduped (two real coffees)
    for _ in range(2):
        r = client.post(
            "/transactions",
            data={
                "date": "2026-04-20",
                "amount": "15.00",
                "direction": "spend",
                "vendor": "Same Shop",
                "description": "Same Shop",
            },
            follow_redirects=False,
        )
        assert r.status_code == 303

    import moneypit.db as db
    with db.connect() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM transactions WHERE vendor='Same Shop'").fetchone()["n"]
    assert n == 2, "manual duplicates must not be squashed by the dedup hash"


def test_manual_auto_category_from_rules(client):
    # "Biedronka" should auto-map to Groceries by the default rules
    r = client.post(
        "/transactions",
        data={
            "date": "2026-04-20",
            "amount": "50.00",
            "direction": "spend",
            "vendor": "Biedronka",
            "category": "",  # auto
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    import moneypit.db as db
    with db.connect() as conn:
        row = conn.execute("SELECT category FROM transactions WHERE vendor='Biedronka'").fetchone()
    assert row["category"] == "Groceries"


def test_manual_zero_amount_rejected(client):
    r = client.post(
        "/transactions",
        data={
            "date": "2026-04-20",
            "amount": "0",
            "direction": "spend",
            "vendor": "Nothing",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert "error=" in r.headers["location"]
