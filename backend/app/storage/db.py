"""SQLite connection handling and schema.

The store is deliberately thin. Only the *raw ingested rows* are persisted;
reconciliation and analytics are recomputed from them on every read. Derived
totals are never written to a table, so there is no second copy of the truth to
drift out of sync with the first.
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

#: One connection for the whole process, guarded by a lock.
#:
#: A thread-local connection would be the usual choice, but it is wrong for an
#: in-memory database: ``:memory:`` is scoped to its connection, so each request
#: thread would silently get an empty database of its own. Sharing one connection
#: keeps every worker looking at the same data in both modes, and the lock below
#: keeps the explicit transactions from interleaving.
_connection: sqlite3.Connection | None = None
_connection_path: str | None = None
_lock = threading.RLock()


def db_path() -> str:
    return os.environ.get("DB_PATH", DEFAULT_DB_PATH)


def _configure(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    # Enforce the cascade from billing_days to visits rather than trusting callers.
    conn.execute("PRAGMA foreign_keys = ON")
    # isolation_level=None hands transaction control to transaction(), which
    # needs an explicit BEGIN IMMEDIATE around a day replacement.
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
def transaction() -> Iterator[sqlite3.Connection]:
    """Run a block inside one immediate transaction, serialised across threads.

    ``BEGIN IMMEDIATE`` takes the write lock up front, so two overlapping
    ingests of the same day cannot interleave into a mixture of both.
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
