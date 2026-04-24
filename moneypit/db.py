import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("MONEYPIT_DB", "moneypit.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE,
    color TEXT
);

CREATE TABLE IF NOT EXISTS transactions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT NOT NULL,           -- ISO YYYY-MM-DD
    amount        REAL NOT NULL,           -- negative = spend
    currency      TEXT NOT NULL DEFAULT 'PLN',
    description   TEXT NOT NULL,           -- raw bank description
    vendor        TEXT,                    -- cleaned vendor name
    category      TEXT,
    op_type       TEXT,                    -- e.g. 'card', 'blik', 'transfer'
    source        TEXT NOT NULL,           -- 'csv' | 'gmail' | 'receipt'
    source_bank   TEXT,                    -- 'pekao' | 'ing' | 'mbank' | NULL
    source_ref    TEXT,                    -- filename or similar
    profile_id    INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
    hash          TEXT NOT NULL UNIQUE,    -- dedup key
    imported_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tx_date      ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_category  ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_tx_vendor    ON transactions(vendor);
CREATE INDEX IF NOT EXISTS idx_tx_profile   ON transactions(profile_id);

CREATE TABLE IF NOT EXISTS rules (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern   TEXT NOT NULL,               -- lowercase substring match on description+vendor
    category  TEXT NOT NULL,
    vendor    TEXT,                        -- optional canonical vendor name
    priority  INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS categories (
    name  TEXT PRIMARY KEY,
    color TEXT
);
"""

DEFAULT_PROFILE_NAME = "Me"
DEFAULT_PROFILE_COLOR = "#60a5fa"

DEFAULT_CATEGORIES = [
    ("Groceries",       "#4ade80"),
    ("Eating out",      "#fb923c"),
    ("Transport",       "#60a5fa"),
    ("Entertainment",   "#c084fc"),
    ("Shopping",        "#f472b6"),
    ("Bills & Utilities", "#fbbf24"),
    ("Subscriptions",   "#a78bfa"),
    ("Health",          "#34d399"),
    ("Income",          "#10b981"),
    ("Transfers",       "#94a3b8"),
    ("Uncategorized",   "#64748b"),
]

# Starter rules tuned against real Pekao exports. Patterns are lowercase
# substrings matched against `description + " " + vendor` (also lowercased).
# Many Polish merchants appear under their legal/operator name on statements,
# not their consumer brand — hence "jmp s.a." for Biedronka, "terg" for
# Media Expert, "lpp" for LPP brands (House/Reserved/Cropp/Sinsay), etc.
DEFAULT_RULES = [
    # --- Groceries ---
    ("biedronka",   "Groceries", "Biedronka"),
    ("jmp s.a",     "Groceries", "Biedronka"),          # Jeronimo Martins Polska
    ("jeronimo",    "Groceries", "Biedronka"),
    ("lidl",        "Groceries", "Lidl"),
    ("kaufland",    "Groceries", "Kaufland"),
    ("carrefour",   "Groceries", "Carrefour"),
    ("auchan",      "Groceries", "Auchan"),
    ("zabka",       "Groceries", "Żabka"),
    ("żabka",       "Groceries", "Żabka"),
    ("netto",       "Groceries", "Netto"),
    ("dino",        "Groceries", "Dino"),
    ("stokrotka",   "Groceries", "Stokrotka"),
    ("piekarnia",   "Groceries", "Piekarnia"),          # generic "bakery"
    ("piotr i pawel", "Groceries", "Piotr i Paweł"),

    # --- Eating out ---
    ("mcdonald",    "Eating out", "McDonald's"),
    ("kfc",         "Eating out", "KFC"),
    ("burger king", "Eating out", "Burger King"),
    ("pizza hut",   "Eating out", "Pizza Hut"),
    ("dominos",     "Eating out", "Domino's"),
    ("subway",      "Eating out", "Subway"),
    ("starbucks",   "Eating out", "Starbucks"),
    ("costa coffee","Eating out", "Costa Coffee"),
    ("pyszne",      "Eating out", "Pyszne.pl"),
    ("uber eats",   "Eating out", "Uber Eats"),
    ("glovo",       "Eating out", "Glovo"),
    ("wolt",        "Eating out", "Wolt"),

    # --- Transport ---
    ("uber",        "Transport", "Uber"),
    ("bolt.eu",     "Transport", "Bolt"),
    ("bolt ",       "Transport", "Bolt"),
    ("orlen",       "Transport", "Orlen"),
    ("bp ",         "Transport", "BP"),
    ("shell",       "Transport", "Shell"),
    ("lotos",       "Transport", "Lotos"),
    ("circle k",    "Transport", "Circle K"),
    ("moya",        "Transport", "Moya"),
    ("pkp",         "Transport", "PKP"),
    ("intercity",   "Transport", "PKP Intercity"),
    ("flixbus",     "Transport", "FlixBus"),
    ("skycash",     "Transport", "SkyCash"),            # parking/transit tickets
    ("mpk ",        "Transport", "MPK"),
    ("myjnia",      "Transport", "Car Wash"),           # user had "MYJNIA AWIX OIL"

    # --- Subscriptions / entertainment ---
    ("netflix",     "Subscriptions", "Netflix"),
    ("spotify",     "Subscriptions", "Spotify"),
    ("youtube",     "Subscriptions", "YouTube Premium"),
    ("disney",      "Subscriptions", "Disney+"),
    ("hbo",         "Subscriptions", "HBO Max"),
    ("canal+",      "Subscriptions", "Canal+"),
    ("canal +",     "Subscriptions", "Canal+"),
    ("player.pl",   "Subscriptions", "Player.pl"),
    ("tidal",       "Subscriptions", "Tidal"),
    ("openai",      "Subscriptions", "OpenAI"),
    ("anthropic",   "Subscriptions", "Anthropic"),
    ("github",      "Subscriptions", "GitHub"),
    ("jetbrains",   "Subscriptions", "JetBrains"),
    ("apple.com/bill", "Subscriptions", "Apple"),       # iCloud/App Store subs
    ("google *",    "Subscriptions", "Google"),
    ("displate",    "Subscriptions", "Displate"),

    # --- Shopping ---
    ("allegro",     "Shopping", "Allegro"),
    ("aliexpress",  "Shopping", "AliExpress"),
    ("amazon",      "Shopping", "Amazon"),
    ("amzn",        "Shopping", "Amazon"),
    ("zalando",     "Shopping", "Zalando"),
    ("media expert","Shopping", "Media Expert"),
    ("mediaexpert", "Shopping", "Media Expert"),
    ("terg ",       "Shopping", "Media Expert"),        # TERG S.A. = Media Expert
    ("rtv euro",    "Shopping", "RTV Euro AGD"),
    ("x-kom",       "Shopping", "x-kom"),
    ("morele",      "Shopping", "Morele"),
    ("ikea",        "Shopping", "IKEA"),
    ("leroy merlin","Shopping", "Leroy Merlin"),
    ("castorama",   "Shopping", "Castorama"),
    ("obi ",        "Shopping", "OBI"),
    ("empik",       "Shopping", "Empik"),
    ("rossmann",    "Shopping", "Rossmann"),
    ("hebe",        "Shopping", "Hebe"),
    ("sephora",     "Shopping", "Sephora"),
    # LPP group — House, Reserved, Cropp, Sinsay, Mohito all appear as "LPP ..."
    ("lpp ",        "Shopping", "LPP (Reserved/House/Cropp/Sinsay)"),
    ("reserved",    "Shopping", "Reserved"),
    ("sinsay",      "Shopping", "Sinsay"),
    ("cropp",       "Shopping", "Cropp"),
    ("house ",      "Shopping", "House"),
    ("mohito",      "Shopping", "Mohito"),
    ("ccc ",        "Shopping", "CCC"),
    ("deichmann",   "Shopping", "Deichmann"),
    ("sportsdirect","Shopping", "SportsDirect"),
    ("decathlon",   "Shopping", "Decathlon"),
    ("guess",       "Shopping", "Guess"),
    ("h&m",         "Shopping", "H&M"),
    ("whaleco",     "Shopping", "Temu"),                # Temu's legal entity
    ("temu",        "Shopping", "Temu"),
    ("shein",       "Shopping", "Shein"),

    # --- Bills & Utilities ---
    ("orange",      "Bills & Utilities", "Orange"),
    ("play ",       "Bills & Utilities", "Play"),
    ("p4 sp",       "Bills & Utilities", "Play"),        # P4 Sp. z o.o. = Play
    ("t-mobile",    "Bills & Utilities", "T-Mobile"),
    ("plus ",       "Bills & Utilities", "Plus"),
    ("tauron",      "Bills & Utilities", "Tauron"),
    ("pgnig",       "Bills & Utilities", "PGNiG"),
    ("pgn",         "Bills & Utilities", "PGNiG"),
    ("energa",      "Bills & Utilities", "Energa"),
    ("enea ",       "Bills & Utilities", "Enea"),
    ("pge ",        "Bills & Utilities", "PGE"),
    ("vectra",      "Bills & Utilities", "Vectra"),
    ("upc ",        "Bills & Utilities", "UPC"),
    ("inea",        "Bills & Utilities", "INEA"),
    ("netia",       "Bills & Utilities", "Netia"),

    # --- Health / insurance ---
    ("pzu",         "Health", "PZU"),                    # insurance
    ("warta",       "Health", "Warta"),
    ("medicover",   "Health", "Medicover"),
    ("luxmed",      "Health", "LUX MED"),
    ("lux med",     "Health", "LUX MED"),
    ("enel-med",    "Health", "Enel-Med"),
    ("apteka",      "Health", "Apteka"),                 # generic "pharmacy"
    ("dr.max",      "Health", "Dr.Max"),
    ("ziko",        "Health", "Ziko Apteka"),

    # --- Transfers / financial plumbing (not real spend) ---
    ("spłata kredytu", "Transfers", None),
    ("splata kredytu", "Transfers", None),
    ("przelew własny", "Transfers", None),
    ("przelew wlasny", "Transfers", None),
    ("bank pekao s.a", "Bills & Utilities", "Bank Pekao"),  # account fees
    ("alior bank blik", "Transfers", None),              # BLIK top-ups between accounts
    ("prowizja",       "Bills & Utilities", "Bank Fee"),

    # --- Payment processors — intentionally NOT categorized ---
    # PayU, PayPro, PayNow, Nuvei etc. are intermediaries — the real merchant
    # is usually hidden. We leave these uncategorized so the user notices
    # and can create a specific rule with more context.
]


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _ensure_profile_column(conn: sqlite3.Connection) -> None:
    """Add `profile_id` to `transactions` on pre-existing DBs. `CREATE TABLE
    IF NOT EXISTS` doesn't alter an existing table, so we check and patch."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(transactions)")}
    if "profile_id" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tx_profile ON transactions(profile_id)")


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _ensure_profile_column(conn)

        existing = {row["name"] for row in conn.execute("SELECT name FROM categories")}
        for name, color in DEFAULT_CATEGORIES:
            if name not in existing:
                conn.execute("INSERT INTO categories (name, color) VALUES (?, ?)", (name, color))

        rule_count = conn.execute("SELECT COUNT(*) AS n FROM rules").fetchone()["n"]
        if rule_count == 0:
            conn.executemany(
                "INSERT INTO rules (pattern, category, vendor) VALUES (?, ?, ?)",
                DEFAULT_RULES,
            )

        # Seed the default profile and stamp any orphan transactions onto it,
        # so existing DBs upgrade cleanly without leaving rows unassigned.
        default_id_row = conn.execute(
            "SELECT id FROM profiles WHERE name = ?", (DEFAULT_PROFILE_NAME,)
        ).fetchone()
        if default_id_row is None:
            cur = conn.execute(
                "INSERT INTO profiles (name, color) VALUES (?, ?)",
                (DEFAULT_PROFILE_NAME, DEFAULT_PROFILE_COLOR),
            )
            default_id = cur.lastrowid
        else:
            default_id = default_id_row["id"]
        conn.execute(
            "UPDATE transactions SET profile_id = ? WHERE profile_id IS NULL",
            (default_id,),
        )
