"""
Tests for the /transactions browser page.
"""
import os
import tempfile
from datetime import date
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

    # Seed a predictable range of transactions so we can test filtering +
    # pagination without re-seeding in every test.
    with db.connect() as conn:
        for i in range(120):
            d = f"2026-0{1 + i % 3}-{1 + (i % 28):02d}"  # spread across Jan-Mar
            amt = -10.0 - i  # negative, unique
            vendor = "BIEDRONKA" if i % 3 == 0 else ("SPOTIFY" if i % 3 == 1 else "NETFLIX")
            cat = "Groceries" if i % 3 == 0 else "Subscriptions"
            conn.execute(
                """INSERT INTO transactions
                   (date, amount, currency, description, vendor, category, source, hash)
                   VALUES (?, ?, 'PLN', ?, ?, ?, 'csv', ?)""",
                (d, amt, vendor, vendor, cat, f"h{i}"),
            )

    import moneypit.main
    importlib.reload(moneypit.main)
    from fastapi.testclient import TestClient
    yield TestClient(moneypit.main.app)
    Path(path).unlink(missing_ok=True)


def test_default_is_all_time(client):
    r = client.get("/transactions")
    assert r.status_code == 200
    assert "All time" in r.text
    # We seeded 120 rows, page size is 50, so we should see page 1 of 3.
    assert "Page 1 of 3" in r.text


def test_category_filter(client):
    r = client.get("/transactions?category=Groceries")
    assert r.status_code == 200
    assert "BIEDRONKA" in r.text
    assert "SPOTIFY" not in r.text
    assert "NETFLIX" not in r.text


def test_search_filter(client):
    r = client.get("/transactions?q=spotify")
    assert r.status_code == 200
    assert "SPOTIFY" in r.text
    assert "BIEDRONKA" not in r.text


def test_date_range_filter(client):
    # Only January
    r = client.get("/transactions?from=2026-01-01&to=2026-01-31")
    assert r.status_code == 200
    # February shouldn't show up anywhere in the table
    assert "2026-02-" not in r.text


def test_pagination_preserves_filters(client):
    r = client.get("/transactions?category=Subscriptions&page=1")
    assert r.status_code == 200
    # Groceries rows should not appear on a Subscriptions page
    assert "BIEDRONKA" not in r.text
    # Link to page 2 must carry the category filter
    assert "category=Subscriptions" in r.text
    assert "page=2" in r.text


def test_empty_filter_result(client):
    r = client.get("/transactions?q=thisvendordefinitelydoesnotexist")
    assert r.status_code == 200
    assert "No transactions match" in r.text


def test_inline_categorize_still_works_on_transactions_page(client):
    # Date it after the seeded batch (which tops out at 2026-03-28) so it
    # appears on page 1 regardless of seed ordering.
    import moneypit.db as db
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO transactions
               (date, amount, currency, description, vendor, category, source, hash)
               VALUES ('2026-04-15', -50.0, 'PLN', 'MYSTERY SHOP', 'MYSTERY SHOP',
                       'Uncategorized', 'csv', 'mysteryhash')"""
        )
        tx_id = conn.execute("SELECT id FROM transactions WHERE hash = 'mysteryhash'").fetchone()["id"]

    r = client.get("/transactions")
    assert "MYSTERY SHOP" in r.text
    assert f"/transactions/{tx_id}/categorize" in r.text
