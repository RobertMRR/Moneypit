from moneypit.importers.csv_import import parse_csv, detect_bank


# Synthetic fixture — all account numbers, reference IDs, and card masks
# are fake. Do not replace with real exports (this file is committed).
PEKAO_SAMPLE = (
    "Data księgowania;Data waluty;Nadawca / Odbiorca;Adres nadawcy / odbiorcy;"
    "Rachunek źródłowy;Rachunek docelowy;Tytułem;Kwota operacji;Waluta;"
    "Numer referencyjny;Typ operacji;Kategoria\n"
    "23.04.2026;23.04.2026;ALIEXPRESS.COM Luxembourg;;'00000000000000000000000000;;"
    "*********0000000;-65,08;PLN;'C0000000000000000;TRANSAKCJA KARTĄ PŁATNICZĄ;Bez kategorii\n"
    "22.04.2026;21.04.2026;TERG SPOLKA AKCYJNA;WWW.MEDIAEXPERT ZA DWORCEM 1D,ZLOTOW;"
    "'00000000000000000000000000;;BLIK REF       00000000000;-249,00;PLN;"
    "'C000000000000000;PŁATNOŚĆ BLIK;Bez kategorii\n"
).encode("utf-8")


def test_detect_pekao():
    header = [
        "Data księgowania", "Data waluty", "Nadawca / Odbiorca",
        "Adres nadawcy / odbiorcy", "Rachunek źródłowy", "Rachunek docelowy",
        "Tytułem", "Kwota operacji", "Waluta", "Numer referencyjny",
        "Typ operacji", "Kategoria",
    ]
    assert detect_bank(header) == "pekao"


def test_parse_pekao_sample():
    bank, txs = parse_csv(PEKAO_SAMPLE, source_ref="test.csv")
    assert bank == "pekao"
    assert len(txs) == 2

    ali = txs[0]
    assert ali.date.isoformat() == "2026-04-23"
    assert ali.amount == -65.08
    assert ali.currency == "PLN"
    assert ali.vendor == "ALIEXPRESS.COM Luxembourg"
    assert ali.op_type == "card"
    assert ali.source_bank == "pekao"

    terg = txs[1]
    assert terg.amount == -249.00
    assert terg.op_type == "blik"


def test_dedup_hash_is_stable():
    _, txs_a = parse_csv(PEKAO_SAMPLE)
    _, txs_b = parse_csv(PEKAO_SAMPLE)
    assert txs_a[0].hash_key() == txs_b[0].hash_key()
    assert txs_a[0].hash_key() != txs_a[1].hash_key()
