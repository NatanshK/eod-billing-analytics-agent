"""SQLite connection handling and schema.

Only raw ingested rows are persisted. Reconciliation and analytics are recomputed
on every read, so there is no stored total that can drift from the data it
summarises.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_DB_PATH = ":memory:"

SCHEMA = """
CREATE TABLE IF NOT EXISTS billing_days (
    clinic_id     TEXT NOT NULL,
    business_date TEXT NOT NULL,
    payload_hash  TEXT NOT NULL,
    row_count     INTEGER NOT NULL,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    -- Rows that failed validation are kept with the day, not discarded. Every
    -- report response carries them so the totals are never quietly short.
    rejected_json TEXT NOT NULL DEFAULT '[]',
    rows_received INTEGER NOT NULL DEFAULT 0,
    ingested_at   TEXT NOT NULL,
    PRIMARY KEY (clinic_id, business_date)
);

CREATE TABLE IF NOT EXISTS visits (
    clinic_id     TEXT NOT NULL,
    business_date TEXT NOT NULL,
    row_index     INTEGER NOT NULL,
    visit_id      TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    PRIMARY KEY (clinic_id, business_date, visit_id),
    FOREIGN KEY (clinic_id, business_date)
        REFERENCES billing_days (clinic_id, business_date) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_visits_day ON visits (clinic_id, business_date);

-- The narrative is the one derived artefact worth caching: it costs a model
-- call. It is keyed by the hash of the data it described, so a re-ingest can
-- never leave a stale summary attached to corrected numbers.
CREATE TABLE IF NOT EXISTS narratives (
    clinic_id     TEXT NOT NULL,
    business_date TEXT NOT NULL,
    payload_hash  TEXT NOT NULL,
    narrative_json TEXT NOT NULL,
    generated_at  TEXT NOT NULL,
    PRIMARY KEY (clinic_id, business_date)
);
"""

#: One connection for the whole process, guarded by a lock. A thread-local
#: connection is the usual choice but is wrong for ":memory:", which is scoped to
#: its connection — each request thread would get an empty database of its own.
_connection: sqlite3.Connection | None = None
_connection_path: str | None = None
_lock = threading.RLock()


def db_path() -> str:
    return os.environ.get("DB_PATH", DEFAULT_DB_PATH)


def _configure(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # Hands transaction control to transaction(), which needs an explicit
    # BEGIN IMMEDIATE around a day replacement.
    conn.isolation_level = None
    return conn


def get_connection() -> sqlite3.Connection:
    global _connection, _connection_path

    path = db_path()
    with _lock:
        if _connection is not None and _connection_path == path:
            return _connection

        if _connection is not None:
            _connection.close()

        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)

        _connection = _configure(sqlite3.connect(path, check_same_thread=False))
        _connection.executescript(SCHEMA)
        _connection_path = path
        return _connection


@contextmanager
def serialised() -> Iterator[sqlite3.Connection]:
    """Hold the connection lock without opening a transaction.

    The connection is process-wide, so a statement issued while another thread
    holds BEGIN IMMEDIATE gets enlisted in that thread's transaction and can be
    rolled back with it. Reads need the guard too, or they can observe a day
    replacement half-applied.
    """
    conn = get_connection()
    with _lock:
        yield conn


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Run a block in one immediate transaction, serialised across threads.

    BEGIN IMMEDIATE takes the write lock up front, so two overlapping ingests of
    the same day cannot interleave.
    """
    conn = get_connection()
    with _lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")


def reset() -> None:
    """Drop the cached connection. Used between tests."""
    global _connection, _connection_path

    with _lock:
        if _connection is not None:
            _connection.close()
        _connection = None
        _connection_path = None
