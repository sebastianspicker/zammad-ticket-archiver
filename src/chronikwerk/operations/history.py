"""Derive bounded operator job history from the request journal.

A FAILED row with ``error_code`` ``not_triggered`` (the ticket lacked the trigger tag) is shown
as ``skipped`` rather than ``failed``.
"""

from __future__ import annotations

from typing import Any, Protocol

from chronikwerk.operations.journal import JournalRequest, RequestJournal, RequestState
from chronikwerk.redaction import scrub_secrets_in_text

_DEFAULT_MAX_ENTRIES = 5000
_STATUS_BY_STATE = {
    RequestState.WAITING: "accepted",
    RequestState.COLLECTING: "running",
    RequestState.VERIFYING: "running",
    RequestState.COMPLETE: "processed",
    RequestState.FAILED: "failed",
    RequestState.COALESCED: "coalesced",
}
_SKIPPED = "skipped"
_NOT_TRIGGERED = "not_triggered"


class HistoryReader(Protocol):
    """Read side of job history consumed by operator views."""

    def read(
        self,
        limit: int,
        ticket_id: int | None = None,
        *,
        before_id: int | None = None,
        statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the newest matching entries, newest first."""
        ...


def _matches_status(status: str, statuses: set[str] | None) -> bool:
    if not statuses:
        return True
    return any(status == item or status.startswith(f"{item}_") for item in statuses)


class EmptyHistory:
    """History of a read-only application without a scheduler: always empty."""

    def read(
        self,
        limit: int,
        ticket_id: int | None = None,
        *,
        before_id: int | None = None,
        statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return no entries."""
        return []


def _is_skipped(request: JournalRequest) -> bool:
    return request.state is RequestState.FAILED and request.error_code == _NOT_TRIGGERED


def _status(request: JournalRequest) -> str:
    return _SKIPPED if _is_skipped(request) else _STATUS_BY_STATE[request.state]


def _classification(request: JournalRequest) -> str | None:
    if _is_skipped(request):
        return None
    if request.state is RequestState.FAILED:
        return "Permanent"
    if request.state is RequestState.WAITING and request.error_code:
        return "Transient"
    return None


def _message(request: JournalRequest) -> str:
    parts = [part for part in (request.error_code, request.error_detail) if part]
    return scrub_secrets_in_text(": ".join(parts))


def _journal_entry(rowid: int, request: JournalRequest) -> dict[str, Any]:
    return {
        "id": str(rowid),
        "status": _status(request),
        "ticket_id": request.ticket_id,
        "classification": _classification(request),
        "message": _message(request),
        "delivery_id": request.delivery_id,
        "request_id": request.request_id,
        "created_at": request.received_at.timestamp(),
        "updated_at": request.updated_at.timestamp(),
    }


class JournalHistoryView:
    """Durable operator history: one entry per journaled request, keyed by SQLite rowid."""

    def __init__(self, journal: RequestJournal, *, max_entries: int = _DEFAULT_MAX_ENTRIES) -> None:
        self._journal = journal
        self._max_entries = max_entries

    def read(
        self,
        limit: int,
        ticket_id: int | None = None,
        *,
        before_id: int | None = None,
        statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the newest matching requests, newest first; ``id`` is a ``before_id`` cursor."""
        bounded_limit = max(0, min(int(limit), self._max_entries))
        states = None
        not_triggered = None
        if statuses:
            states = [
                state for state, name in _STATUS_BY_STATE.items() if _matches_status(name, statuses)
            ]
            want_failed = RequestState.FAILED in states
            want_skipped = _matches_status(_SKIPPED, statuses)
            if want_skipped and not want_failed:
                states.append(RequestState.FAILED)
            if want_failed != want_skipped:
                not_triggered = want_skipped
        if bounded_limit == 0 or states == []:
            return []
        rows = self._journal.list_page(
            bounded_limit, ticket_id, before_id, states, not_triggered=not_triggered
        )
        return [_journal_entry(rowid, request) for rowid, request in rows]
