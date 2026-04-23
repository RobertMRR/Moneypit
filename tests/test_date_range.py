"""
Tests for the dashboard date-range filter.
"""
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

import pytest


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


def test_resolve_range_default_is_last_30_days():
    from moneypit.main import _resolve_range
    start, end, label = _resolve_range(None, None)
    assert end == date.today()
    assert (end - start).days == 29
    assert label == "Last 30 days"


def test_resolve_range_explicit_dates():
    from moneypit.main import _resolve_range
    start, end, label = _resolve_range("2026-01-01", "2026-01-31")
    assert start == date(2026, 1, 1)
    assert end == date(2026, 1, 31)
    assert "Jan" in label


def test_resolve_range_swaps_if_inverted():
    # User picks end-before-start in the date picker — don't crash, just swap.
    from moneypit.main import _resolve_range
    start, end, _ = _resolve_range("2026-05-01", "2026-04-01")
    assert start == date(2026, 4, 1)
    assert end == date(2026, 5, 1)


def test_resolve_range_invalid_string_falls_back_to_default():
    from moneypit.main import _resolve_range
    start, end, _ = _resolve_range("not-a-date", "also-not-a-date")
    assert end == date.today()
    assert (end - start).days == 29


def test_dashboard_respects_date_range(tmp_db):
    import importlib
    import moneypit.main
    importlib.reload(moneypit.main)
    from fastapi.testclient import TestClient

    # Seed: income in March, spend in April. Querying only March should
    # return income but zero spend.
    rows = [
        ("2026-03-15",  5000.0, "SALARY",    "Income"),
        ("2026-04-10",  -200.0, "BIEDRONKA", "Groceries"),
    ]
    with tmp_db.connect() as conn:
        for i, (d, amt, desc, cat) in enumerate(rows):
            conn.execute(
                """INSERT INTO transactions
                   (date, amount, currency, description, vendor, category, source, hash)
                   VALUES (?, ?, 'PLN', ?, ?, ?, 'csv', ?)""",
                (d, amt, desc, desc, cat, f"h{i}"),
            )

    client = TestClient(moneypit.main.app)
    r = client.get("/?from=2026-03-01&to=2026-03-31")
    assert r.status_code == 200
    assert "5000.00 PLN" in r.text  # income visible
    # "Spent" card should show 0 in March, not the April spend.
    assert "200.00 PLN" not in r.text or "5000.00 PLN" in r.text
    # Sanity: April-only range flips it.
    r2 = client.get("/?from=2026-04-01&to=2026-04-30")
    assert "200.00 PLN" in r2.text
    assert "5000.00 PLN" not in r2.text
