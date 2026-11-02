"""Index archived revisions in SQLite, in the same database file as the request journal.

The catalog is an index, not the record: ``rebuild_from_store`` recreates the ``revisions``
table from the manifests in the archive store. The request journal owns ``PRAGMA
user_version``; the catalog tracks its own schema version in ``catalog_meta``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from chronikwerk.failures import PermanentError
from chronikwerk.preservation.manifest import (
    MANIFEST_NAME,
    SEAL_NAME,
    TOKEN_NAME,
    Manifest,
    ManifestError,
    parse_manifest,
)
from chronikwerk.preservation.revision import PackageLayoutError, revision_directory_name
from chronikwerk.storage.archive_store import ArchiveStore, ArchiveStoreError
from chronikwerk.timestamps import format_time, now_utc

__all__ = ["ArchiveCatalog", "CatalogError", "CatalogRevision", "RebuildReport"]

CATALOG_VERSION = 1
_BUSY_TIMEOUT_MS = 5000
_FILE_MODE = 0o600
_MANIFEST_SUFFIX = "/" + MANIFEST_NAME
_TICKET_DIR_PREFIX = "Ticket-"
_MISSING_IN_STORE = "missing in store"

_SCHEMA_STATEMENTS = (
    "CREATE TABLE IF NOT EXISTS catalog_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
    """
    CREATE TABLE IF NOT EXISTS revisions (
        revision_id TEXT PRIMARY KEY,
        ticket_id INTEGER NOT NULL,
        ticket_number TEXT NOT NULL,
        revision_number INTEGER NOT NULL,
        record_class TEXT NOT NULL,
        archive_user TEXT NOT NULL,
        archive_path TEXT NOT NULL,
        key_prefix TEXT NOT NULL,
        captured_at TEXT NOT NULL,
        source_digest TEXT NOT NULL,
        policy_id TEXT NOT NULL,
        manifest_sha256 TEXT NOT NULL,
        retain_until TEXT,
        retention_enforced INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        verified_at TEXT,
        verification_result TEXT,
        UNIQUE (ticket_id, revision_number)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS revisions_match
    ON revisions (ticket_id, source_digest, policy_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS holds (
        hold_id INTEGER PRIMARY KEY AUTOINCREMENT,
        revision_id TEXT,
        ticket_id INTEGER,
        authority TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at TEXT NOT NULL,
        review_date TEXT,
        released_at TEXT,
        released_by TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS dispositions (
        disposition_id INTEGER PRIMARY KEY AUTOINCREMENT,
        revision_id TEXT NOT NULL,
        approval_ref TEXT NOT NULL,
        approved_by TEXT NOT NULL,
        disposed_at TEXT NOT NULL,
        key_prefix TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS access_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        at TEXT NOT NULL,
        actor TEXT NOT NULL,
        action TEXT NOT NULL,
        revision_id TEXT,
        outcome TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS fixity_checks (
        revision_id TEXT NOT NULL,
        checked_at TEXT NOT NULL,
        result TEXT NOT NULL,
        detail TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS renewals (
        revision_id TEXT NOT NULL,
        renewal_number INTEGER NOT NULL,
        renewed_at TEXT NOT NULL,
        token_key TEXT NOT NULL,
        PRIMARY KEY (revision_id, renewal_number)
    )
    """,
)

_COLUMNS = (
    "revision_id, ticket_id, ticket_number, revision_number, record_class, archive_user, "
    "archive_path, key_prefix, captured_at, source_digest, policy_id, manifest_sha256, "
    "retain_until, retention_enforced, verified_at, verification_result"
)
_INSERT_SQL = f"INSERT INTO revisions ({_COLUMNS}) VALUES ({', '.join('?' * 16)})"
# A rebuild refreshes everything the store knows; verification results stay as recorded.
_UPSERT_SQL = (
    _INSERT_SQL
    + " ON CONFLICT (revision_id) DO UPDATE SET "
    + ", ".join(f"{column} = excluded.{column}" for column in _COLUMNS.split(", ")[1:14])
)


class CatalogError(PermanentError):
    """Raised when a catalog operation is invalid or the catalog cannot be opened."""


@dataclass(frozen=True, slots=True)
class CatalogRevision:
    """One archived revision as indexed by the catalog."""

    revision_id: str
    ticket_id: int
    ticket_number: str
    revision_number: int
    record_class: str
    archive_user: str
    archive_path: tuple[str, ...]
    key_prefix: str
    captured_at: datetime
    source_digest: str
    policy_id: str
    manifest_sha256: str
    retain_until: datetime | None
    retention_enforced: bool
    verified_at: datetime | None
    verification_result: str | None


@dataclass(frozen=True, slots=True)
class RebuildReport:
    """Outcome of ``rebuild_from_store``; ``skipped`` holds ``(key prefix, reason)`` pairs."""

    added: int
    updated: int
    skipped: tuple[tuple[str, str], ...]


class _Skipped(Exception):  # noqa: N818 - internal control flow, not an error type
    """A manifest in the store that cannot be cataloged; carries the reason."""


def _format_optional(value: datetime | None) -> str | None:
    return None if value is None else format_time(value)


def _parse_optional(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def _to_revision(row: sqlite3.Row) -> CatalogRevision:
    captured_at = datetime.fromisoformat(row["captured_at"])
    return CatalogRevision(
        revision_id=row["revision_id"],
        ticket_id=row["ticket_id"],
        ticket_number=row["ticket_number"],
        revision_number=row["revision_number"],
        record_class=row["record_class"],
        archive_user=row["archive_user"],
        archive_path=tuple(json.loads(row["archive_path"])),
        key_prefix=row["key_prefix"],
        captured_at=captured_at,
        source_digest=row["source_digest"],
        policy_id=row["policy_id"],
        manifest_sha256=row["manifest_sha256"],
        retain_until=_parse_optional(row["retain_until"]),
        retention_enforced=bool(row["retention_enforced"]),
        verified_at=_parse_optional(row["verified_at"]),
        verification_result=row["verification_result"],
    )


def _revision_values(revision: CatalogRevision) -> tuple[Any, ...]:
    return (
        revision.revision_id,
        revision.ticket_id,
        revision.ticket_number,
        revision.revision_number,
        revision.record_class,
        revision.archive_user,
        json.dumps(list(revision.archive_path)),
        revision.key_prefix,
        format_time(revision.captured_at),
        revision.source_digest,
        revision.policy_id,
        revision.manifest_sha256,
        _format_optional(revision.retain_until),
        int(revision.retention_enforced),
        _format_optional(revision.verified_at),
        revision.verification_result,
    )


def _open_database(path: Path) -> sqlite3.Connection:
    """Open ``path`` (creating it with mode 0600) and ensure the catalog tables exist."""
    if not path.parent.is_dir():
        raise CatalogError(f"Catalog directory does not exist: {path.parent}")
    try:
        os.close(os.open(path, os.O_CREAT | os.O_RDWR, _FILE_MODE))
        conn = sqlite3.connect(
            path,
            isolation_level=None,
            check_same_thread=False,
            timeout=_BUSY_TIMEOUT_MS / 1000,
        )
    except (OSError, sqlite3.Error) as exc:
        raise CatalogError(f"Cannot open catalog database {path}: {exc}") from exc
    try:
        _configure(conn)
    except BaseException:
        conn.close()
        raise
    return conn


def _configure(conn: sqlite3.Connection) -> None:
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for statement in _SCHEMA_STATEMENTS:
            conn.execute(statement)
        _check_version(conn)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _check_version(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT value FROM catalog_meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO catalog_meta (key, value) VALUES ('schema_version', ?)",
            (str(CATALOG_VERSION),),
        )
    elif int(row[0]) != CATALOG_VERSION:
        raise CatalogError(
            f"Unsupported catalog schema version {row[0]} (expected {CATALOG_VERSION})"
        )


def _split_layout(prefix: str) -> tuple[str, str, tuple[str, ...], str, str]:
    """Return ``(record_class, archive_user, archive_path, ticket_number, revision_dir)``."""
    segments = prefix.rstrip("/").split("/")
    if len(segments) < 4 or not segments[-2].startswith(_TICKET_DIR_PREFIX):
        raise _Skipped("key prefix does not follow <class>/<user>/<path>/Ticket-<n>/r<NNNN>_<id>")
    return (
        segments[0],
        segments[1],
        tuple(segments[2:-2]),
        segments[-2][len(_TICKET_DIR_PREFIX) :],
        segments[-1],
    )


def _check_layout(manifest: Manifest, record_class: str, number: str, directory: str) -> None:
    if manifest.record_class != record_class:
        raise _Skipped("record class in the key prefix disagrees with the manifest")
    if manifest.ticket.number != number:
        raise _Skipped("ticket number in the key prefix disagrees with the manifest")
    try:
        expected = revision_directory_name(manifest.revision_number, manifest.revision_id)
    except PackageLayoutError as exc:
        raise _Skipped(f"manifest revision cannot form a directory name: {exc}") from exc
    if directory != expected:
        raise _Skipped("revision directory in the key prefix disagrees with the manifest")


def _read_object(store: ArchiveStore, key: str) -> bytes:
    with store.open(key) as handle:
        return handle.read()


def _revision_from_store(store: ArchiveStore, key: str, keys: set[str]) -> CatalogRevision:
    prefix = key[: -len(MANIFEST_NAME)]
    if f"{prefix}{SEAL_NAME}" not in keys or f"{prefix}{TOKEN_NAME}" not in keys:
        raise _Skipped("seal or timestamp token missing")
    record_class, user, path, number, directory = _split_layout(prefix)
    data = _read_object(store, key)
    try:
        manifest = parse_manifest(data)
    except ManifestError as exc:
        raise _Skipped(f"manifest cannot be parsed: {exc}") from exc
    _check_layout(manifest, record_class, number, directory)
    return CatalogRevision(
        revision_id=manifest.revision_id,
        ticket_id=manifest.ticket.id,
        ticket_number=manifest.ticket.number,
        revision_number=manifest.revision_number,
        record_class=record_class,
        archive_user=user,
        archive_path=path,
        key_prefix=prefix,
        captured_at=manifest.captured_at,
        source_digest=manifest.source_digest,
        policy_id=manifest.policy_id,
        manifest_sha256=hashlib.sha256(data).hexdigest(),
        retain_until=store.head(key).retain_until,
        retention_enforced=store.retention_enforced,
        verified_at=None,
        verification_result=None,
    )


class ArchiveCatalog:
    """Thread-safe SQLite index of archived revisions."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        """Open or create the catalog tables in the database file at ``path``."""
        self._clock = clock
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = _open_database(path)
        self._conn.row_factory = sqlite3.Row

    def __enter__(self) -> Self:
        """Return the open catalog."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the catalog."""
        self.close()

    def close(self) -> None:
        """Close the connection; closing twice is harmless."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._conn
            if conn is None:
                raise CatalogError("Catalog is closed")
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def _query(self, sql: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
        with self._lock:
            if self._conn is None:
                raise CatalogError("Catalog is closed")
            return self._conn.execute(sql, params).fetchall()

    def _query_one(self, sql: str, params: tuple[Any, ...]) -> CatalogRevision | None:
        rows = self._query(sql, params)
        return _to_revision(rows[0]) if rows else None

    def add_revision(self, revision: CatalogRevision) -> None:
        """Insert one revision; a duplicate id or ticket/revision number raises."""
        try:
            with self._transaction() as conn:
                conn.execute(_INSERT_SQL, _revision_values(revision))
        except sqlite3.IntegrityError as exc:
            raise CatalogError(
                f"Revision {revision.revision_id} (ticket {revision.ticket_id}, "
                f"number {revision.revision_number}) is already cataloged"
            ) from exc

    def get(self, revision_id: str) -> CatalogRevision | None:
        """Return the revision with ``revision_id`` or None."""
        return self._query_one("SELECT * FROM revisions WHERE revision_id = ?", (revision_id,))

    def find_matching(
        self, *, ticket_id: int, source_digest: str, policy_id: str
    ) -> CatalogRevision | None:
        """Return the newest revision of a ticket with the same source digest and policy."""
        return self._query_one(
            "SELECT * FROM revisions WHERE ticket_id = ? AND source_digest = ? AND policy_id = ? "
            "ORDER BY revision_number DESC LIMIT 1",
            (ticket_id, source_digest, policy_id),
        )

    def latest_for_ticket(self, ticket_id: int) -> CatalogRevision | None:
        """Return the highest-numbered revision of a ticket or None."""
        return self._query_one(
            "SELECT * FROM revisions WHERE ticket_id = ? ORDER BY revision_number DESC LIMIT 1",
            (ticket_id,),
        )

    def next_revision_number(self, ticket_id: int) -> int:
        """Return the number the next revision of a ticket should carry (1 when none)."""
        rows = self._query(
            "SELECT MAX(revision_number) AS latest FROM revisions WHERE ticket_id = ?",
            (ticket_id,),
        )
        latest = rows[0]["latest"]
        return 1 if latest is None else int(latest) + 1

    def search(
        self,
        *,
        ticket_number: str | None = None,
        record_class: str | None = None,
        captured_from: datetime | None = None,
        captured_to: datetime | None = None,
        limit: int = 100,
    ) -> list[CatalogRevision]:
        """Return revisions matching every given filter, newest capture first."""
        clauses: list[str] = []
        params: list[Any] = []
        filters = (
            ("ticket_number = ?", ticket_number),
            ("record_class = ?", record_class),
            ("captured_at >= ?", _format_optional(captured_from)),
            ("captured_at <= ?", _format_optional(captured_to)),
        )
        for clause, value in filters:
            if value is not None:
                clauses.append(clause)
                params.append(value)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._query(
            f"SELECT * FROM revisions{where} ORDER BY captured_at DESC, revision_number DESC "
            "LIMIT ?",
            (*params, limit),
        )
        return [_to_revision(row) for row in rows]

    def record_verification(self, revision_id: str, *, result: str, verified_at: datetime) -> None:
        """Store the latest verification result and append a fixity check."""
        stamp = format_time(verified_at)
        with self._transaction() as conn:
            cursor = conn.execute(
                "UPDATE revisions SET verified_at = ?, verification_result = ? "
                "WHERE revision_id = ?",
                (stamp, result, revision_id),
            )
            if cursor.rowcount == 0:
                raise CatalogError(f"Unknown revision {revision_id}")
            conn.execute(
                "INSERT INTO fixity_checks (revision_id, checked_at, result) VALUES (?, ?, ?)",
                (revision_id, stamp, result),
            )

    def record_access(
        self, *, actor: str, action: str, revision_id: str | None, outcome: str
    ) -> None:
        """Append one access event stamped with the catalog clock."""
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO access_events (at, actor, action, revision_id, outcome) "
                "VALUES (?, ?, ?, ?, ?)",
                (format_time(self._clock()), actor, action, revision_id, outcome),
            )

    def export_to(self, target: Path) -> None:
        """Copy the database to ``target`` with the SQLite backup API; ``target`` must not exist."""
        if target.exists():
            raise CatalogError(f"Export target already exists: {target}")
        with self._lock:
            if self._conn is None:
                raise CatalogError("Catalog is closed")
            try:
                os.close(os.open(target, os.O_CREAT | os.O_EXCL | os.O_RDWR, _FILE_MODE))
                destination = sqlite3.connect(target)
                try:
                    self._conn.backup(destination)
                finally:
                    destination.close()
            except (OSError, sqlite3.Error) as exc:
                raise CatalogError(f"Cannot export catalog to {target}: {exc}") from exc

    def rebuild_from_store(
        self, store: ArchiveStore, *, record_classes: Iterable[str]
    ) -> RebuildReport:
        """Recreate ``revisions`` rows from the sealed manifests below each record class.

        Rows for revisions absent from the store are kept and reported as skipped.
        """
        classes = tuple(record_classes)
        found, skipped = _collect_from_store(store, classes)
        added, updated = self._apply_rebuild(found, skipped, classes)
        return RebuildReport(added, updated, tuple(skipped))

    def _apply_rebuild(
        self,
        found: list[CatalogRevision],
        skipped: list[tuple[str, str]],
        classes: tuple[str, ...],
    ) -> tuple[int, int]:
        added = updated = 0
        with self._transaction() as conn:
            for revision in found:
                reason = _conflict(conn, revision)
                if reason is not None:
                    skipped.append((revision.key_prefix, reason))
                    continue
                exists = _exists(conn, revision.revision_id)
                conn.execute(_UPSERT_SQL, _revision_values(revision))
                added, updated = (added, updated + 1) if exists else (added + 1, updated)
            seen = {revision.key_prefix for revision in found} | {p for p, _ in skipped}
            skipped.extend(_missing_in_store(conn, classes, seen))
        return added, updated


def _collect_from_store(
    store: ArchiveStore, record_classes: tuple[str, ...]
) -> tuple[list[CatalogRevision], list[tuple[str, str]]]:
    keys: set[str] = set()
    for record_class in record_classes:
        try:
            keys.update(store.list(f"{record_class}/"))
        except (ArchiveStoreError, FileNotFoundError) as exc:
            raise CatalogError(f"Cannot list record class {record_class!r}: {exc}") from exc
    found: list[CatalogRevision] = []
    skipped: list[tuple[str, str]] = []
    for key in sorted(k for k in keys if k.endswith(_MANIFEST_SUFFIX)):
        try:
            found.append(_revision_from_store(store, key, keys))
        except _Skipped as exc:
            skipped.append((key[: -len(MANIFEST_NAME)], str(exc)))
    return found, skipped


def _exists(conn: sqlite3.Connection, revision_id: str) -> bool:
    row = conn.execute("SELECT 1 FROM revisions WHERE revision_id = ?", (revision_id,)).fetchone()
    return row is not None


def _conflict(conn: sqlite3.Connection, revision: CatalogRevision) -> str | None:
    row = conn.execute(
        "SELECT revision_id FROM revisions WHERE ticket_id = ? AND revision_number = ? "
        "AND revision_id != ?",
        (revision.ticket_id, revision.revision_number, revision.revision_id),
    ).fetchone()
    if row is None:
        return None
    return f"ticket revision number already cataloged as {row[0]}"


def _missing_in_store(
    conn: sqlite3.Connection, record_classes: tuple[str, ...], seen: set[str]
) -> list[tuple[str, str]]:
    rows = conn.execute("SELECT key_prefix, record_class FROM revisions ORDER BY key_prefix")
    return [
        (row["key_prefix"], _MISSING_IN_STORE)
        for row in rows
        if row["record_class"] in record_classes and row["key_prefix"] not in seen
    ]
