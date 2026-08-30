"""Maintain optional bounded in-memory job history for operator views."""

from __future__ import annotations

import time
from collections import deque
from itertools import count
from typing import Any

from chronikwerk.configuration.redaction import scrub_secrets_in_text

_MAX_HISTORY = 5000
_HISTORY: deque[dict[str, Any]] = deque(maxlen=_MAX_HISTORY)
_HISTORY_IDS = count(1)


def _matches_status(status: str, statuses: set[str] | None) -> bool:
    if not statuses:
        return True
    return any(status == item or status.startswith(f"{item}_") for item in statuses)


def _matches_history_filters(
    item: dict[str, Any],
    *,
    ticket_id: int | None,
    before_id: int | None,
    statuses: set[str] | None,
) -> bool:
    """Return whether a history item matches all optional operator filters."""
    if ticket_id is not None and item["ticket_id"] != ticket_id:
        return False
    if before_id is not None and int(item["id"]) >= before_id:
        return False
    return _matches_status(str(item["status"]), statuses)


def record_history_event(
    status: str,
    ticket_id: int | None,
    classification: str | None = None,
    message: str | None = None,
    delivery_id: str | None = None,
    request_id: str | None = None,
) -> None:
    """Record one bounded, non-secret history event for operators."""
    _HISTORY.append(
        {
            "id": str(next(_HISTORY_IDS)),
            "status": status,
            "ticket_id": ticket_id,
            "classification": classification,
            "message": scrub_secrets_in_text(message or ""),
            "delivery_id": delivery_id,
            "request_id": request_id,
            "created_at": time.time(),
        }
    )


def read_history(
    limit: int,
    ticket_id: int | None = None,
    *,
    before_id: int | None = None,
    statuses: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return a safe copy of the optional in-memory job history."""
    bounded_limit = max(0, min(int(limit), _MAX_HISTORY))
    items = [
        item
        for item in reversed(_HISTORY)
        if _matches_history_filters(
            item,
            ticket_id=ticket_id,
            before_id=before_id,
            statuses=statuses,
        )
    ]
    return items[:bounded_limit]


def reset_for_tests() -> None:
    """Clear process-local diagnostic state between isolated tests."""
    global _HISTORY_IDS
    _HISTORY.clear()
    _HISTORY_IDS = count(1)
