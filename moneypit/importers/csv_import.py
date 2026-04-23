"""
CSV importer. Auto-detects Pekao / ING / mBank by header row, then dispatches
to the matching parser.

Currently implemented: Pekao.
Stubs: ING, mBank — paste a sample header row and the parsers can be filled in.
"""
import csv
import io
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

from ..db import connect
from ..models import Transaction


# --- Header signatures for bank detection ---------------------------------
PEKAO_HEADERS = {
    "Data księgowania", "Data waluty", "Nadawca / Odbiorca",
    "Kwota operacji", "Waluta", "Typ operacji",
}

# TODO: fill in once we have a sample ING export
ING_HEADERS: set[str] = set()

# TODO: fill in once we have a sample mBank export
MBANK_HEADERS: set[str] = set()


def detect_bank(header: list[str]) -> Optional[str]:
    header_set = {h.strip() for h in header}
    if PEKAO_HEADERS.issubset(header_set):
        return "pekao"
    if ING_HEADERS and ING_HEADERS.issubset(header_set):
        return "ing"
    if MBANK_HEADERS and MBANK_HEADERS.issubset(header_set):
        return "mbank"
    return None


# --- Helpers --------------------------------------------------------------
def _parse_pl_amount(s: str) -> float:
    """'-249,00' -> -249.00  |  '1 234,56' -> 1234.56"""
    cleaned = s.replace(" ", "").replace(" ", "").replace(",", ".")
    return float(cleaned)


def _parse_pl_date(s: str) -> datetime.date:
    """'23.04.2026' -> date(2026, 4, 23)"""
    return datetime.strptime(s.strip(), "%d.%m.%Y").date()


def _strip_excel_quote(s: str) -> str:
    """Pekao prefixes account numbers with a single quote. Drop it."""
    return s[1:] if s.startswith("'") else s


def _classify_op_type(raw: str) -> str:
    r = raw.lower()
    if "blik" in r:
        return "blik"
    if "kartą" in r or "karta" in r:
        return "card"
    if "przelew" in r:
        return "transfer"
    if "prowizja" in r or "opłata" in r:
        return "fee"
    return "other"


# --- Bank-specific parsers ------------------------------------------------
def _parse_pekao(reader: csv.DictReader, source_ref: str) -> Iterable[Transaction]:
    for row in reader:
        try:
            d = _parse_pl_date(row["Data księgowania"])
            amount = _parse_pl_amount(row["Kwota operacji"])
        except (KeyError, ValueError):
            continue  # skip malformed rows rather than crashing the whole import

        vendor_raw = (row.get("Nadawca / Odbiorca") or "").strip()
        title = (row.get("Tytułem") or "").strip()
        description = vendor_raw or title or "(no description)"

        yield Transaction(
            date=d,
            amount=amount,
            currency=(row.get("Waluta") or "PLN").strip() or "PLN",
            description=description,
            vendor=vendor_raw or None,
            op_type=_classify_op_type(row.get("Typ operacji") or ""),
            source="csv",
            source_bank="pekao",
            source_ref=source_ref,
        )


def _parse_ing(reader: csv.DictReader, source_ref: str) -> Iterable[Transaction]:
    raise NotImplementedError(
        "ING parser not yet implemented — paste a sample CSV header row to fill this in."
    )


def _parse_mbank(reader: csv.DictReader, source_ref: str) -> Iterable[Transaction]:
    raise NotImplementedError(
        "mBank parser not yet implemented — paste a sample CSV header row to fill this in."
    )


# --- Public API -----------------------------------------------------------
def _decode_bank_csv(content: bytes) -> str:
    """
    Polish bank CSVs are inconsistent: Pekao exports in Windows-1250,
    ING/mBank more often in UTF-8. Try strict UTF-8 first; if it fails,
    fall back to cp1250 which is the correct encoding for Pekao.
    """
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1250")


def parse_csv(content: bytes, source_ref: str = "upload") -> tuple[str, list[Transaction]]:
    """
    Parse a bank CSV from raw bytes. Returns (bank_name, transactions).
    Raises ValueError if the bank can't be identified.
    """
    text = _decode_bank_csv(content)
    # Sniff delimiter — Pekao uses ';', others might use ','
    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if reader.fieldnames is None:
        raise ValueError("CSV has no header row")

    bank = detect_bank(list(reader.fieldnames))
    if bank is None:
        raise ValueError(
            f"Could not identify bank from CSV headers: {reader.fieldnames}"
        )

    parsers = {"pekao": _parse_pekao, "ing": _parse_ing, "mbank": _parse_mbank}
    transactions = list(parsers[bank](reader, source_ref))
    return bank, transactions


def import_csv_file(path: Path) -> dict:
    """Parse + insert. Returns a summary dict."""
    from ..categorize import apply_rules

    content = path.read_bytes()
    bank, txs = parse_csv(content, source_ref=path.name)

    inserted = 0
    skipped = 0
    with connect() as conn:
        for tx in txs:
            apply_rules(tx, conn)
            try:
                conn.execute(
                    """INSERT INTO transactions
                       (date, amount, currency, description, vendor, category,
                        op_type, source, source_bank, source_ref, hash)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        tx.date.isoformat(), tx.amount, tx.currency,
                        tx.description, tx.vendor, tx.category,
                        tx.op_type, tx.source, tx.source_bank, tx.source_ref,
                        tx.hash_key(),
                    ),
                )
                inserted += 1
            except Exception as e:
                # Unique constraint on hash = already imported, silently skip.
                if "UNIQUE" in str(e):
                    skipped += 1
                else:
                    raise

    return {"bank": bank, "parsed": len(txs), "inserted": inserted, "skipped": skipped}
