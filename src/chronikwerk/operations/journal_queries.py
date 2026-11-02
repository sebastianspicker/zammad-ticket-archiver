"""Hold the request journal's connection, clock and read-side queries.

``RequestJournal`` extends ``JournalQueries`` with the state transitions; every method here
only reads rows, except ``purge_terminal``, which deletes finished rows.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Self

from chronikwerk.failures import PermanentError
from chronikwerk.operations.journal_types import (
    JournalRequest,
    RequestSource,
    RequestState,
    ZammadStatus,
)
from chronikwerk.timestamps import format_time

_PURGE_VICTIMS = (
    "((state IN ('complete', 'failed') OR (state = 'coalesced' AND coalesced_into IN "
    "(SELECT request_id FROM requests WHERE state IN ('complete', 'failed')))) "
    "AND updated_at < ?)"
)


class JournalStateError(PermanentError):
    """Raised when a journal operation is illegal for the row's current state."""


def parse_time(value: str | None) -> datetime | None:
    """Parse a stored journal time; ``None`` stays ``None``."""
    return None if value is None else datetime.fromisoformat(value)


def to_request(row: sqlite3.Row) -> JournalRequest:
    """Convert one ``requests`` row into a ``JournalRequest``."""
    received_at = parse_time(row["received_at"])
    updated_at = parse_time(row["updated_at"])
    assert received_at is not None and updated_at is not None
    return JournalRequest(
        request_id=row["request_id"],
        ticket_id=row["ticket_id"],
        delivery_id=row["delivery_id"],
        source=RequestSource(row["source"]),
        received_at=received_at,
        updated_at=updated_at,
        state=RequestState(row["state"]),
        attempts=row["attempts"],
        next_attempt_at=parse_time(row["next_attempt_at"]),
        coalesced_into=row["coalesced_into"],
        revision_id=row["revision_id"],
        revision_number=row["revision_number"],
        commit_step=row["commit_step"],
        zammad_status=ZammadStatus(row["zammad_status"]),
        error_code=row["error_code"],
        error_detail=row["error_detail"],
        evidence_json=row["evidence_json"],
        context_json=row["context_json"],
        trigger_login=row["trigger_login"],
    )


class JournalQueries:
    """Thread-safe access to the journal connection plus every read-side query."""

    def __init__(self, conn: sqlite3.Connection, *, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = conn
        self._conn.row_factory = sqlite3.Row

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the database connection; further use raises."""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("Journal clock must return timezone-aware datetimes")
        return value.astimezone(UTC)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._conn
            if conn is None:
                raise JournalStateError("Journal is closed")
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                # Never mask the original error with a failing rollback.
                with suppress(sqlite3.Error):
                    conn.execute("ROLLBACK")
                raise

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            if self._conn is None:
                raise JournalStateError("Journal is closed")
            return self._conn.execute(sql, params).fetchall()

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[JournalRequest]:
        return [to_request(row) for row in self._rows(sql, params)]

    def get(self, request_id: str) -> JournalRequest | None:
        """Return one row, or ``None`` when unknown."""
        rows = self._query("SELECT * FROM requests WHERE request_id = ?", (request_id,))
        return rows[0] if rows else None

    def list_recent(self, *, limit: int = 100) -> list[JournalRequest]:
        """Return the most recently received rows, newest first."""
        return self._query(
            "SELECT * FROM requests ORDER BY received_at DESC, rowid DESC LIMIT ?", (limit,)
        )

    def list_page(
        self,
        limit: int,
        ticket_id: int | None = None,
        before_rowid: int | None = None,
        states: Sequence[RequestState] | None = None,
        *,
        not_triggered: bool | None = None,
    ) -> list[tuple[int, JournalRequest]]:
        """Return up to ``limit`` ``(rowid, row)`` pairs below ``before_rowid``, newest first.

        ``not_triggered`` keeps only (``True``) or drops (``False``) FAILED rows whose
        ``error_code`` is ``not_triggered``; rows in other states are unaffected.
        """
        wanted = None if states is None else json.dumps([state.value for state in states])
        rows = self._rows(
            "SELECT rowid AS row_number, * FROM requests WHERE (?1 IS NULL OR ticket_id = ?1) "
            "AND (?2 IS NULL OR rowid < ?2) "
            "AND (?3 IS NULL OR state IN (SELECT value FROM json_each(?3))) "
            "AND (?5 IS NULL OR state != 'failed' "
            "OR (COALESCE(error_code, '') = 'not_triggered') = ?5) "
            "ORDER BY rowid DESC LIMIT ?4",
            (ticket_id, before_rowid, wanted, limit, not_triggered),
        )
        return [(row["row_number"], to_request(row)) for row in rows]

    def count_by_state(self) -> dict[RequestState, int]:
        """Return the row count for every state, including zero counts."""
        rows = self._rows("SELECT state, COUNT(*) FROM requests GROUP BY state")
        counts = {RequestState(state): count for state, count in rows}
        return {state: counts.get(state, 0) for state in RequestState}

    def oldest_waiting_age_seconds(self) -> float | None:
        """Return the age of the oldest WAITING row, or ``None`` when none wait."""
        row = self._rows("SELECT MIN(received_at) FROM requests WHERE state = 'waiting'")[0]
        oldest = parse_time(row[0])
        if oldest is None:
            return None
        return max(0.0, (self._now() - oldest).total_seconds())

    def pending_zammad_updates(self) -> list[JournalRequest]:
        """Return COMPLETE rows whose Zammad note is still pending or failed."""
        return self._query(
            "SELECT * FROM requests WHERE state = 'complete' "
            "AND zammad_status IN ('pending', 'failed') ORDER BY received_at, rowid"
        )

    def pending_failure_reports(self) -> list[JournalRequest]:
        """Return FAILED rows whose failure has not been reported to Zammad yet."""
        return self._query(
            "SELECT * FROM requests WHERE state = 'failed' "
            "AND zammad_status = 'pending' ORDER BY received_at, rowid"
        )

    def stuck(self, *, older_than_seconds: float) -> list[JournalRequest]:
        """Return COLLECTING/VERIFYING rows not updated for ``older_than_seconds``."""
        cutoff = format_time(self._now() - timedelta(seconds=older_than_seconds))
        return self._query(
            "SELECT * FROM requests WHERE state IN ('collecting', 'verifying') "
            "AND updated_at < ? ORDER BY updated_at, rowid",
            (cutoff,),
        )

    def purge_terminal(self, *, older_than_seconds: float) -> int:
        """Delete finished rows (and their coalesced duplicates) not updated recently."""
        cutoff = format_time(self._now() - timedelta(seconds=older_than_seconds))
        with self._transaction() as conn:
            conn.execute(
                f"UPDATE requests SET coalesced_into = NULL WHERE NOT {_PURGE_VICTIMS} "  # noqa: S608
                f"AND coalesced_into IN (SELECT request_id FROM requests WHERE {_PURGE_VICTIMS})",
                (cutoff, cutoff),
            )
            cursor = conn.execute(
                f"DELETE FROM requests WHERE {_PURGE_VICTIMS}",  # noqa: S608
                (cutoff,),
            )
            return cursor.rowcount
