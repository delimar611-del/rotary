import sqlite3
from pathlib import Path

from .config import DEFAULT_DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    imported_at TEXT NOT NULL,
    source_file TEXT NOT NULL,
    unmatched_file TEXT,
    invoice_count INTEGER NOT NULL,
    open_count INTEGER NOT NULL,
    total_amount_cents INTEGER NOT NULL,
    outstanding_cents INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS partners (
    oib TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL DEFAULT '',
    contact TEXT NOT NULL DEFAULT '',
    payment_terms_days INTEGER,          -- NULL -> config default
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS invoices (
    inv TEXT PRIMARY KEY,
    buyer TEXT NOT NULL,
    buyer_oib TEXT NOT NULL,
    issue_date TEXT NOT NULL,            -- ISO YYYY-MM-DD
    amount_cents INTEGER NOT NULL,
    pay_status TEXT NOT NULL,
    paid_cents INTEGER NOT NULL DEFAULT 0,
    payment_refs TEXT NOT NULL DEFAULT '[]',
    first_seen_run INTEGER NOT NULL REFERENCES runs(id),
    last_seen_run INTEGER NOT NULL REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS invoice_history (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    inv TEXT NOT NULL,
    pay_status TEXT NOT NULL,
    paid_cents INTEGER NOT NULL,
    UNIQUE (run_id, inv)
);

CREATE TABLE IF NOT EXISTS unmatched_payments (
    id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    pay_date TEXT,                       -- ISO YYYY-MM-DD if parseable
    payer TEXT NOT NULL DEFAULT '',
    amount_cents INTEGER NOT NULL,
    reference TEXT NOT NULL DEFAULT '',
    raw TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open', -- open | resolved | dismissed
    first_seen_run INTEGER NOT NULL REFERENCES runs(id),
    last_seen_run INTEGER NOT NULL REFERENCES runs(id)
);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY,
    oib TEXT NOT NULL REFERENCES partners(oib),
    level INTEGER NOT NULL,
    invoices TEXT NOT NULL,              -- JSON list of invoice numbers
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    outstanding_cents INTEGER NOT NULL,
    drafted_at TEXT NOT NULL,
    sent_at TEXT                          -- set manually by the user, app never sends
);
"""


def connect(db_path=None):
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn
