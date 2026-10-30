"""Create and version the SQLite database that backs the request journal."""

from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path

from chronikwerk.failures import PermanentError

SCHEMA_VERSION = 1
_BUSY_TIMEOUT_MS = 5000
_FILE_MODE = 0o600

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE requests (
        request_id TEXT PRIMARY KEY,
        ticket_id INTEGER NOT NULL,
        delivery_id TEXT,
        source TEXT NOT NULL,
        received_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        state TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT,
        coalesced_into TEXT REFERENCES requests(request_id),
        revision_id TEXT,
        revision_number INTEGER,
        commit_step TEXT,
        zammad_status TEXT NOT NULL DEFAULT 'pending',
        error_code TEXT,
        error_detail TEXT,
        evidence_json TEXT,
        merged_into TEXT,
        context_json TEXT,
        trigger_login TEXT
    )
    """,
    """
    CREATE UNIQUE INDEX requests_one_active_per_ticket
    ON requests (ticket_id)
    WHERE state IN ('waiting', 'collecting', 'verifying')
    """,
    """
    CREATE UNIQUE INDEX requests_unique_delivery
    ON requests (delivery_id)
    WHERE delivery_id IS NOT NULL
    """,
    "CREATE INDEX requests_state_next ON requests (state, next_attempt_at)",
)


def open_database(path: Path) -> sqlite3.Connection:
    """Open (and create) the journal database at ``path`` with private permissions."""
    _check_parent(path.parent)
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, _FILE_MODE)
        try:
            os.fchmod(fd, _FILE_MODE)
        finally:
            os.close(fd)
        conn = sqlite3.connect(
            path,
            isolation_level=None,
            check_same_thread=False,
            timeout=_BUSY_TIMEOUT_MS / 1000,
        )
    except (OSError, sqlite3.Error) as exc:
        raise PermanentError(f"Cannot open journal database {path}: {exc}") from exc
    try:
        _configure(conn)
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise PermanentError(f"Cannot use journal database {path}: {exc}") from exc
    except BaseException:
        conn.close()
        raise
    return conn


def _check_parent(parent: Path) -> None:
    if not parent.is_dir():
        raise PermanentError(f"Journal directory does not exist: {parent}")
    mode = parent.stat().st_mode
    if mode & stat.S_IWOTH and not mode & stat.S_ISVTX:
        raise PermanentError(f"Journal directory is world-writable without sticky bit: {parent}")


def _configure(conn: sqlite3.Connection) -> None:
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if str(mode).lower() != "wal":
        raise PermanentError(f"Journal database cannot use WAL mode (got {mode})")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("BEGIN IMMEDIATE")
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version == 0:
            for statement in _SCHEMA_STATEMENTS:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        elif version != SCHEMA_VERSION:
            raise PermanentError(
                f"Unsupported journal schema version {version} (expected {SCHEMA_VERSION})"
            )
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
