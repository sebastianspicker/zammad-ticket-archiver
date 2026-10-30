"""Provide a durable SQLite journal of admitted archive requests.

Coalescing rule: a request that arrives while another request for the same ticket is active is
stored as COALESCED (at most ``MAX_COALESCED_PER_TICKET`` per ticket; further ones are dropped
because the active capture will already see the newest ticket state). When the active row reaches
a terminal state, in the same transaction, the oldest COALESCED row of that ticket is promoted to
WAITING and every other COALESCED row is re-pointed to it. A COALESCED row without evidence is a
plain duplicate: it is also marked ``merged_into`` the promoted row, and when that row completes
the duplicate becomes COMPLETE with the same revision (its Zammad status is DONE, because the
promoted row posts the single note). Only rows that already existed at promotion are merged, as
the promoted capture starts later and therefore covers them. Rows with ``evidence_json`` are
never merged away: they stay COALESCED and are promoted one by one.

Back-off rule: ``attempts`` counts failures. After ``retry_max_attempts`` automatic retries the
next failure ends in FAILED; earlier failures wait ``base * 2**attempts`` seconds, capped.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from chronikwerk.operations.journal_queries import (
    JournalQueries,
    JournalStateError,
    format_time,
    to_request,
)
from chronikwerk.operations.journal_schema import open_database
from chronikwerk.operations.journal_types import (
    AdmitItem,
    AdmitResult,
    JournalRequest,
    RequestSource,
    RequestState,
    ZammadStatus,
)
from chronikwerk.timestamps import now_utc

__all__ = [
    "MAX_COALESCED_PER_TICKET",
    "MAX_ERROR_DETAIL_CHARS",
    "MAX_EVIDENCE_CHARS",
    "MAX_TRIGGER_LOGIN_CHARS",
    "AdmitItem",
    "AdmitResult",
    "JournalOptions",
    "JournalRequest",
    "JournalStateError",
    "RequestJournal",
    "RequestSource",
    "RequestState",
    "ZammadStatus",
]

_ACTIVE_SQL = "('waiting', 'collecting', 'verifying')"
MAX_COALESCED_PER_TICKET = 20
MAX_EVIDENCE_CHARS = 65536
MAX_ERROR_DETAIL_CHARS = 4096
MAX_TRIGGER_LOGIN_CHARS = 255
_IDLE_FIELDS = dict.fromkeys(("commit_step", "revision_id", "revision_number", "context_json"))


class _BatchFull(Exception):
    """Roll back an ``admit_many`` transaction whose batch does not fit."""


@dataclass(frozen=True, slots=True)
class JournalOptions:
    """Describe where the journal lives and how transient failures back off."""

    path: Path
    retry_max_attempts: int = 5
    retry_max_delay_seconds: float = 3600.0
    retry_base_delay_seconds: float = 30.0


def _check_evidence(evidence_json: str | None) -> None:
    if len(evidence_json or "") > MAX_EVIDENCE_CHARS:
        raise ValueError(f"evidence_json exceeds {MAX_EVIDENCE_CHARS} characters")


def _clip(detail: str) -> str:
    return detail[:MAX_ERROR_DETAIL_CHARS]


def _login(value: str | None) -> str | None:
    return value[:MAX_TRIGGER_LOGIN_CHARS] if value else None


class RequestJournal(JournalQueries):
    """Durable, thread-safe journal of admitted requests backed by SQLite."""

    def __init__(
        self,
        options: JournalOptions,
        *,
        clock: Callable[[], datetime] = now_utc,
    ) -> None:
        self._options = options
        super().__init__(open_database(options.path), clock=clock)

    @property
    def retry_max_attempts(self) -> int:
        """Return how many automatic retries a request gets before it fails."""
        return self._options.retry_max_attempts

    @staticmethod
    def _fetch(conn: sqlite3.Connection, request_id: str) -> JournalRequest:
        row = conn.execute("SELECT * FROM requests WHERE request_id = ?", (request_id,)).fetchone()
        if row is None:
            raise JournalStateError(f"Unknown request: {request_id}")
        return to_request(row)

    def _update(
        self,
        conn: sqlite3.Connection,
        request_id: str,
        allowed: tuple[RequestState, ...],
        **fields: Any,
    ) -> JournalRequest:
        """Apply ``fields`` to a row that must currently be in one of ``allowed``."""
        current = self._fetch(conn, request_id)
        if current.state not in allowed:
            raise JournalStateError(
                f"Request {request_id} is {current.state.value}; "
                f"expected one of {[state.value for state in allowed]}"
            )
        fields["updated_at"] = format_time(self._now())
        assignments = ", ".join(f"{name} = ?" for name in fields)
        conn.execute(
            f"UPDATE requests SET {assignments} WHERE request_id = ?",  # noqa: S608
            (*fields.values(), request_id),
        )
        return self._fetch(conn, request_id)

    def _insert(
        self,
        conn: sqlite3.Connection,
        item: AdmitItem,
        *,
        state: RequestState,
        coalesced_into: str | None,
    ) -> str:
        request_id = str(uuid.uuid7())
        now = format_time(self._now())
        conn.execute(
            "INSERT INTO requests (request_id, ticket_id, delivery_id, source, received_at, "
            "updated_at, state, coalesced_into, evidence_json, trigger_login) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                request_id,
                item.ticket_id,
                item.delivery_id,
                item.source.value,
                now,
                now,
                state.value,
                coalesced_into,
                item.evidence_json,
                _login(item.trigger_login),
            ),
        )
        return request_id

    def admit(
        self,
        *,
        ticket_id: int,
        source: RequestSource,
        delivery_id: str | None = None,
        evidence_json: str | None = None,
        max_pending: int | None = None,
        trigger_login: str | None = None,
    ) -> AdmitResult:
        """Journal one request, deduplicating, coalescing or rejecting it as needed."""
        item = AdmitItem(ticket_id, source, delivery_id, evidence_json, trigger_login)
        _check_evidence(evidence_json)
        with self._transaction() as conn:
            return self._admit_in(conn, item, max_pending)

    def admit_many(
        self, items: Sequence[AdmitItem], *, max_pending: int | None
    ) -> list[AdmitResult] | None:
        """Journal a batch in one transaction; ``None`` (nothing stored) when any item is full."""
        for item in items:
            _check_evidence(item.evidence_json)
        results: list[AdmitResult] = []
        with suppress(_BatchFull), self._transaction() as conn:
            for item in items:
                results.append(self._admit_in(conn, item, max_pending))
                if results[-1].outcome == "full":
                    raise _BatchFull
            return results
        return None

    def _admit_in(
        self, conn: sqlite3.Connection, item: AdmitItem, max_pending: int | None
    ) -> AdmitResult:
        seen = self._delivery_owner(conn, item.delivery_id)
        if seen is not None:
            return AdmitResult("duplicate_delivery", None, seen)
        active = conn.execute(
            f"SELECT request_id FROM requests WHERE ticket_id = ? AND state IN {_ACTIVE_SQL}",  # noqa: S608
            (item.ticket_id,),
        ).fetchone()
        if active is None and self._is_full(conn, max_pending):
            return AdmitResult("full", None, None)
        active_id = None if active is None else active["request_id"]
        if active is not None and self._coalesced_full(conn, item.ticket_id):
            return AdmitResult("coalesced", None, active_id)
        state = RequestState.WAITING if active is None else RequestState.COALESCED
        new_id = self._insert(conn, item, state=state, coalesced_into=active_id)
        return AdmitResult("admitted" if active is None else "coalesced", new_id, active_id)

    @staticmethod
    def _delivery_owner(conn: sqlite3.Connection, delivery_id: str | None) -> str | None:
        if delivery_id is None:
            return None
        row = conn.execute(
            "SELECT request_id FROM requests WHERE delivery_id = ?", (delivery_id,)
        ).fetchone()
        return None if row is None else str(row["request_id"])

    @staticmethod
    def _coalesced_full(conn: sqlite3.Connection, ticket_id: int) -> bool:
        row = conn.execute(
            "SELECT COUNT(*) FROM requests WHERE ticket_id = ? AND state = 'coalesced'",
            (ticket_id,),
        ).fetchone()
        return bool(row[0] >= MAX_COALESCED_PER_TICKET)

    @staticmethod
    def _is_full(conn: sqlite3.Connection, max_pending: int | None) -> bool:
        if max_pending is None:
            return False
        waiting = conn.execute("SELECT COUNT(*) FROM requests WHERE state = 'waiting'").fetchone()
        return bool(waiting[0] >= max_pending)

    def lease_next(self) -> JournalRequest | None:
        """Move the oldest due WAITING request to COLLECTING and return it.

        A row sent back by ``fail_transient(keep_revision=True)`` keeps its revision, commit
        step and commit context, so the processor can resume that commit.
        """
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT request_id FROM requests WHERE state = 'waiting' "
                "AND (next_attempt_at IS NULL OR next_attempt_at <= ?) "
                "ORDER BY received_at, rowid LIMIT 1",
                (format_time(self._now()),),
            ).fetchone()
            if row is None:
                return None
            return self._update(
                conn,
                row["request_id"],
                (RequestState.WAITING,),
                state=RequestState.COLLECTING.value,
                next_attempt_at=None,
            )

    def mark_verifying(
        self,
        request_id: str,
        *,
        revision_id: str,
        revision_number: int,
        context_json: str | None = None,
    ) -> None:
        """Move a COLLECTING request to VERIFYING with the revision and its commit context."""
        with self._transaction() as conn:
            self._update(
                conn,
                request_id,
                (RequestState.COLLECTING,),
                state=RequestState.VERIFYING.value,
                revision_id=revision_id,
                revision_number=revision_number,
                context_json=context_json,
            )

    def record_commit_step(self, request_id: str, step: str) -> None:
        """Remember the last finished commit step of a VERIFYING request."""
        with self._transaction() as conn:
            self._update(conn, request_id, (RequestState.VERIFYING,), commit_step=step)

    def complete(self, request_id: str, *, revision_id: str) -> None:
        """Finish a VERIFYING (or already-archived COLLECTING) request and queue its note."""
        with self._transaction() as conn:
            current = self._fetch(conn, request_id)
            if current.state is RequestState.VERIFYING and current.revision_id != revision_id:
                raise JournalStateError(
                    f"Request {request_id} verifies revision {current.revision_id}, "
                    f"not {revision_id}"
                )
            done = self._update(
                conn,
                request_id,
                (RequestState.COLLECTING, RequestState.VERIFYING),
                state=RequestState.COMPLETE.value,
                revision_id=revision_id,
                zammad_status=ZammadStatus.PENDING.value,
                error_code=None,
                error_detail=None,
            )
            self._settle_coalesced(conn, done)

    def fail_transient(
        self,
        request_id: str,
        *,
        error_code: str,
        error_detail: str,
        keep_revision: bool = False,
    ) -> JournalRequest:
        """Record a retryable failure: back off to WAITING, or FAILED when exhausted.

        With ``keep_revision`` a VERIFYING row that has a commit context keeps its revision,
        commit step and context, so the next attempt resumes that commit; every other row
        returns to WAITING without them.
        """
        options = self._options
        with self._transaction() as conn:
            current = self._fetch(conn, request_id)
            attempts = current.attempts + 1
            if attempts > options.retry_max_attempts:
                return self._fail(conn, request_id, error_code, error_detail, attempts=attempts)
            delay = min(
                options.retry_base_delay_seconds * 2**attempts,
                options.retry_max_delay_seconds,
            )
            keep = (
                keep_revision
                and current.state is RequestState.VERIFYING
                and current.context_json is not None
            )
            return self._update(
                conn,
                request_id,
                (RequestState.COLLECTING, RequestState.VERIFYING),
                state=RequestState.WAITING.value,
                attempts=attempts,
                next_attempt_at=format_time(self._now() + timedelta(seconds=delay)),
                error_code=error_code,
                error_detail=_clip(error_detail),
                **({} if keep else _IDLE_FIELDS),
            )

    def _fail(
        self,
        conn: sqlite3.Connection,
        request_id: str,
        error_code: str,
        error_detail: str,
        **extra: Any,
    ) -> JournalRequest:
        """Mark a COLLECTING/VERIFYING row FAILED and promote its coalesced successors."""
        failed = self._update(
            conn,
            request_id,
            (RequestState.COLLECTING, RequestState.VERIFYING),
            state=RequestState.FAILED.value,
            next_attempt_at=None,
            error_code=error_code,
            error_detail=_clip(error_detail),
            **extra,
        )
        self._settle_coalesced(conn, failed)
        return failed

    def fail_permanent(self, request_id: str, *, error_code: str, error_detail: str) -> None:
        """Mark an in-flight request FAILED without retry."""
        with self._transaction() as conn:
            self._fail(conn, request_id, error_code, error_detail)

    def _settle_coalesced(self, conn: sqlite3.Connection, finished: JournalRequest) -> None:
        """Resolve or promote the COALESCED rows of a ticket whose active row just finished."""
        now = format_time(self._now())
        if finished.state is RequestState.COMPLETE:
            conn.execute(
                "UPDATE requests SET state = 'complete', revision_id = ?, revision_number = ?, "
                "zammad_status = 'done', updated_at = ? WHERE state = 'coalesced' "
                "AND merged_into = ?",
                (finished.revision_id, finished.revision_number, now, finished.request_id),
            )
        rows = conn.execute(
            "SELECT request_id FROM requests WHERE ticket_id = ? AND state = 'coalesced' "
            "ORDER BY received_at, rowid",
            (finished.ticket_id,),
        ).fetchall()
        if not rows:
            return
        promoted = rows[0]["request_id"]
        conn.execute(
            "UPDATE requests SET state = 'waiting', coalesced_into = NULL, merged_into = NULL, "
            "attempts = 0, next_attempt_at = NULL, updated_at = ? WHERE request_id = ?",
            (now, promoted),
        )
        conn.execute(
            "UPDATE requests SET coalesced_into = ?, merged_into = "
            "CASE WHEN evidence_json IS NULL THEN ? END "
            "WHERE ticket_id = ? AND state = 'coalesced'",
            (promoted, promoted, finished.ticket_id),
        )

    def set_zammad_status(self, request_id: str, status: ZammadStatus) -> None:
        """Record the Zammad note status of a COMPLETE (or FAILED, ``not_triggered``) request."""
        allowed = (RequestState.COMPLETE, RequestState.FAILED)
        with self._transaction() as conn:
            self._update(conn, request_id, allowed, zammad_status=status.value)

    def recover_after_restart(self) -> tuple[list[JournalRequest], list[JournalRequest]]:
        """Requeue interrupted COLLECTING rows and return them with the VERIFYING rows.

        An interrupted attempt counts as a failure for both states; a row interrupted more
        often than ``retry_max_attempts`` allows becomes FAILED (``interrupted_repeatedly``,
        its Zammad report still pending) instead. Surviving VERIFYING rows stay VERIFYING.
        """
        with self._transaction() as conn:
            requeued = [
                self._update(
                    conn,
                    request_id,
                    (RequestState.COLLECTING,),
                    state=RequestState.WAITING.value,
                    attempts=attempts,
                    next_attempt_at=None,
                    **_IDLE_FIELDS,
                )
                for request_id, attempts in self._interrupted(conn, RequestState.COLLECTING)
            ]
            verifying = [
                self._update(conn, request_id, (RequestState.VERIFYING,), attempts=attempts)
                for request_id, attempts in self._interrupted(conn, RequestState.VERIFYING)
            ]
        return requeued, verifying

    def _interrupted(self, conn: sqlite3.Connection, state: RequestState) -> list[tuple[str, int]]:
        """Fail rows of ``state`` interrupted too often; return the others' new attempt counts."""
        survivors: list[tuple[str, int]] = []
        for row in conn.execute(
            "SELECT request_id, attempts FROM requests WHERE state = ? ORDER BY received_at, rowid",
            (state.value,),
        ).fetchall():
            attempts = row["attempts"] + 1
            if attempts > self._options.retry_max_attempts:
                self._fail(
                    conn,
                    row["request_id"],
                    "interrupted_repeatedly",
                    "Request was interrupted by restarts too often",
                    attempts=attempts,
                )
            else:
                survivors.append((row["request_id"], attempts))
        return survivors
