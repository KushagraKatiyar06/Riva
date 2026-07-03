# SQLite database setup. Creates the sessions, intel, and runs tables on first
# run and applies any schema migrations needed for existing deployments.

import os
import sqlite3
import threading

DB_PATH  = os.path.join(os.path.dirname(__file__), "riva_intel.db")
_db_lock = threading.Lock()


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with _db_lock, _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id           TEXT PRIMARY KEY,
                target_url   TEXT NOT NULL,
                started_at   TEXT NOT NULL,
                completed_at TEXT,
                status       TEXT DEFAULT 'running',
                pricing_found INTEGER DEFAULT 0,
                docs_found    INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS intel (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   TEXT NOT NULL,
                type         TEXT NOT NULL,
                url          TEXT NOT NULL,
                content      TEXT,
                captured_at  TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
            CREATE TABLE IF NOT EXISTS runs (
                id         TEXT PRIMARY KEY,
                riva_url   TEXT,
                comp_url   TEXT,
                started_at TEXT NOT NULL,
                client_id  TEXT
            );
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_runs_client_date ON runs(client_id, started_at)"
        )
        # ADD COLUMN is safe to re-run - it throws if the column already exists,
        # which we catch and ignore so old deployments get the new columns silently.
        for col, definition in [("ip", "TEXT"), ("client_id", "TEXT")]:
            try:
                conn.execute(f"ALTER TABLE runs ADD COLUMN {col} {definition}")
            except Exception:
                pass
