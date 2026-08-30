"""Verify classified archive failures preserve retry semantics and tag cleanup."""

from __future__ import annotations

import asyncio
import errno
from types import SimpleNamespace
from typing import cast

import pytest

import chronikwerk.archiving.workflow_errors as workflow_errors
from chronikwerk.archiving.options import ArchiveRuntimeOptions
from chronikwerk.archiving.workflow import ArchiveAttempt


class _ErrorClient:
    """Record the operator note attempted by failure handling."""

    def __init__(self, *, reject_note: bool = False) -> None:
        self.note: tuple[int, str, str] | None = None
        self._reject_note = reject_note

    async def create_internal_article(self, ticket_id: int, subject: str, html: str) -> None:
        self.note = (ticket_id, subject, html)
        if self._reject_note:
            raise RuntimeError("note endpoint unavailable")


@pytest.mark.parametrize(
    ("exc", "expected_status", "keep_trigger"),
    [
        (OSError(errno.EAGAIN, "retry later"), "failed_transient", True),
        (ValueError("archive_path must not be empty"), "failed_permanent", False),
    ],
)
def test_failure_outcome_classifies_and_projects_retry_state(
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    expected_status: str,
    keep_trigger: bool,
) -> None:
    client = _ErrorClient(reject_note=True)
    attempt = ArchiveAttempt(
        runtime=cast(ArchiveRuntimeOptions, SimpleNamespace()),
        ticket_id=42,
        delivery_id="delivery-42",
        request_id="request-42",
    )
    applied: list[tuple[int, bool, str]] = []
    history: list[tuple[str, str]] = []

    async def apply_error(
        _client: object,
        ticket_id: int,
        *,
        keep_trigger: bool,
        trigger_tag: str,
    ) -> None:
        applied.append((ticket_id, keep_trigger, trigger_tag))

    def record_history(
        _attempt: ArchiveAttempt,
        *,
        status: str,
        classification: str | None,
        message: str,
    ) -> None:
        history.append((status, classification or ""))

    monkeypatch.setattr(workflow_errors, "apply_error", apply_error)
    monkeypatch.setattr(workflow_errors.workflow, "record_history", record_history)

    outcome = asyncio.run(
        workflow_errors.handle_ticket_pipeline_exception(
            client=client,  # type: ignore[arg-type]
            attempt=attempt,
            trigger_tag="archive:ready",
            exc=exc,
        )
    )

    assert outcome.status == expected_status
    assert outcome.ticket_id == 42
    assert outcome.classification == ("Transient" if keep_trigger else "Permanent")
    assert applied == [(42, keep_trigger, "archive:ready")]
    assert history == [(expected_status, outcome.classification)]
    assert client.note is not None
    assert "delivery-42" in client.note[2]
