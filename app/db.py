import os
import sqlite3
from contextlib import contextmanager
from typing import Iterator

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id    INTEGER PRIMARY KEY,
    name  TEXT UNIQUE NOT NULL,
    color TEXT
);

CREATE TABLE IF NOT EXISTS receipts (
    id                INTEGER PRIMARY KEY,
    barcode           TEXT UNIQUE NOT NULL,
    person_id         INTEGER NOT NULL REFERENCES people(id),
    purchased_at      TEXT NOT NULL,          -- ISO local date-time
    purchase_date     TEXT NOT NULL,          -- YYYY-MM-DD
    warehouse         TEXT,
    kind              TEXT NOT NULL,          -- warehouse | gas | carwash
    is_refund         INTEGER NOT NULL DEFAULT 0,
    subtotal          REAL,
    taxes             REAL,
    total             REAL,
    instant_savings   REAL,
    membership_number TEXT,
    source            TEXT,                   -- costco-json | photo
    raw               TEXT,
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_receipts_date ON receipts(purchase_date);
CREATE INDEX IF NOT EXISTS ix_receipts_person ON receipts(person_id);

CREATE TABLE IF NOT EXISTS products (
    id           INTEGER PRIMARY KEY,
    item_number  TEXT UNIQUE NOT NULL,
    receipt_name TEXT NOT NULL,               -- text as printed on the receipt
    description2 TEXT,                        -- costco.com's 2nd description line (size, "MEMBERSHIP FEE"…)
    department   INTEGER,                     -- costco department number
    name         TEXT,                        -- readable name (AI or manual)
    brand        TEXT,
    category     TEXT,
    return_class TEXT,                        -- standard | electronics_90 | limited | non_returnable
    return_note  TEXT,
    ai_status    TEXT NOT NULL DEFAULT 'pending',  -- pending | done | failed | manual
    group_id     INTEGER                      -- same product under other item numbers (fuzzy match)
);

CREATE TABLE IF NOT EXISTS purchases (
    id         INTEGER PRIMARY KEY,
    receipt_id INTEGER NOT NULL REFERENCES receipts(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    line_no    INTEGER,
    quantity   REAL NOT NULL,                 -- negative on refunds
    unit_price REAL,                          -- shelf price per unit
    amount     REAL NOT NULL,                 -- line total before instant savings
    discount   REAL NOT NULL DEFAULT 0        -- instant savings (negative)
);
CREATE INDEX IF NOT EXISTS ix_purchases_product ON purchases(product_id);
CREATE INDEX IF NOT EXISTS ix_purchases_receipt ON purchases(receipt_id);
"""

COLORS = ["#2563eb", "#db2777", "#059669", "#d97706", "#7c3aed"]


def connect() -> sqlite3.Connection:
    folder = os.path.dirname(settings.db_path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    con = sqlite3.connect(settings.db_path, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    con = connect()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    with get_db() as con:
        con.executescript(SCHEMA)
        cols = {r["name"] for r in con.execute("PRAGMA table_info(products)")}
        for col, typ in (("description2", "TEXT"), ("department", "INTEGER")):
            if col not in cols:
                con.execute(f"ALTER TABLE products ADD COLUMN {col} {typ}")
        if not con.execute("SELECT 1 FROM people").fetchone():
            for i, name in enumerate(settings.people):
                con.execute("INSERT INTO people(name, color) VALUES (?, ?)", (name, COLORS[i % len(COLORS)]))


def add_person(con: sqlite3.Connection, name: str) -> int:
    n = con.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    cur = con.execute("INSERT INTO people(name, color) VALUES (?, ?)", (name, COLORS[n % len(COLORS)]))
    return cur.lastrowid
