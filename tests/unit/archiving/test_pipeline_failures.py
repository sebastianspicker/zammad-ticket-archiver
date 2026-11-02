"""Report pipeline failures once, retry open Zammad updates and resolve the triggering agent."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from chronikwerk.archiving import pipeline_status
from chronikwerk.operations.journal import (
    JournalOptions,
    JournalRequest,
    RequestJournal,
    RequestSource,
    RequestState,
    ZammadStatus,
)
from chronikwerk.operations.scheduling import JournaledSchedulingService
from chronikwerk.timestamps import now_utc
from chronikwerk.zammad.errors import NotFoundError, ServerError
from tests.support.fake_capture import FakeCaptureClient, fixture_ticket
from tests.unit.archiving.test_pipeline import (
    TRIGGER,
    PipelineSetup,
    build_setup,
    lease,
    process,
    revision_of,
)


class _Clock:
    """Mutable journal clock, so back-off retries become due on demand."""

    def __init__(self) -> None:
        self.value = now_utc()

    def __call__(self) -> datetime:
        return self.value

    def advance(self) -> None:
        """Move past any back-off delay."""
        self.value += timedelta(hours=2)


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[PipelineSetup]:
    """Provide a pipeline for a ticket that carries the trigger tag."""
    built = build_setup(tmp_path, FakeCaptureClient(tags=[TRIGGER]))
    yield built
    built.close()


def _rejournal(setup: PipelineSetup, *, retry_max_attempts: int, clock: _Clock) -> RequestJournal:
    """Replace the setup's journal with one using ``retry_max_attempts`` and ``clock``."""
    setup.journal.close()
    setup.journal = RequestJournal(
        JournalOptions(
            path=setup.tmp_path / "chronikwerk.sqlite3", retry_max_attempts=retry_max_attempts
        ),
        clock=clock,
    )
    return setup.journal


def _row(setup: PipelineSetup, request_id: str) -> JournalRequest:
    """Return the current journal row of a request."""
    row = setup.journal.get(request_id)
    assert row is not None
    return row


def _scheduler(setup: PipelineSetup) -> JournaledSchedulingService:
    """Return a scheduler around the real pipeline; only its maintenance is used."""
    return JournaledSchedulingService(
        journal=setup.journal,
        processor=setup.pipeline,
        max_pending=10,
        max_running=1,
        shutdown_timeout_seconds=1.0,
    )


def test_publish_pending_retries_the_zammad_update(
    setup: PipelineSetup, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline_status, "TAG_BACKOFF_BASE_SECONDS", 0.0)
    setup.client.fail_adding_tag = "pdf:signed"
    failed = process(setup)
    assert failed.zammad_status is ZammadStatus.FAILED

    asyncio.run(setup.pipeline.publish_pending(failed))
    still_failed = _row(setup, failed.request_id)
    assert (still_failed.state, still_failed.zammad_status) == (
        RequestState.COMPLETE,
        ZammadStatus.FAILED,
    )
    # The first failure time is kept, so the journal purge ends the retries eventually.
    assert still_failed.updated_at == failed.updated_at
    assert setup.client.notes == []

    setup.client.fail_adding_tag = None
    asyncio.run(setup.pipeline.publish_pending(still_failed))

    row = _row(setup, failed.request_id)
    assert (row.state, row.zammad_status) == (RequestState.COMPLETE, ZammadStatus.DONE)
    assert setup.client.tags == {"pdf:signed"}
    [(subject, body)] = setup.client.notes
    assert "Archive revision complete" in subject
    assert revision_of(setup, row).revision_id in body


def test_publish_pending_ends_when_zammad_no_longer_knows_the_ticket(
    setup: PipelineSetup, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline_status, "TAG_BACKOFF_BASE_SECONDS", 0.0)
    setup.client.fail_adding_tag = "pdf:signed"
    failed = process(setup)
    assert failed.zammad_status is ZammadStatus.FAILED

    async def ticket_gone(ticket_id: int, tag: str) -> None:
        raise NotFoundError(f"Zammad resource not found (status=404) for ticket {ticket_id}")

    monkeypatch.setattr(setup.client, "add_tag", ticket_gone)
    asyncio.run(setup.pipeline.publish_pending(failed))

    row = _row(setup, failed.request_id)
    assert (row.state, row.zammad_status) == (RequestState.COMPLETE, ZammadStatus.DONE)
    assert setup.journal.pending_zammad_updates() == []
    assert setup.client.notes == []


def test_report_failure_notes_a_safety_net_failure_once_zammad_is_reachable(
    setup: PipelineSetup,
) -> None:
    journal = _rejournal(setup, retry_max_attempts=0, clock=_Clock())
    request = lease(setup)
    setup.client.tags.add("pdf:processing")
    # What the scheduler records when the processor left the row in flight.
    journal.fail_transient(
        request.request_id,
        error_code="internal_error",
        error_detail="RuntimeError: cannot open /var/lib/chronikwerk/state.db",
    )
    failed = _row(setup, request.request_id)
    assert (failed.state, failed.zammad_status) == (RequestState.FAILED, ZammadStatus.PENDING)

    setup.client.fail_adding_tag = "pdf:error"
    asyncio.run(setup.pipeline.report_failure(failed))
    assert _row(setup, request.request_id).zammad_status is ZammadStatus.PENDING
    assert setup.client.notes == []

    setup.client.fail_adding_tag = None
    asyncio.run(setup.pipeline.report_failure(_row(setup, request.request_id)))

    row = _row(setup, request.request_id)
    assert (row.state, row.zammad_status) == (RequestState.FAILED, ZammadStatus.DONE)
    assert setup.client.tags == {"pdf:error"}
    [(_, body)] = setup.client.notes
    for text in ("internal_error", "Failed after 1 attempts", "unexpected internal error", TRIGGER):
        assert text in body
    assert "/var/lib" not in body


def test_maintenance_reports_only_failures_the_pipeline_did_not_project(
    setup: PipelineSetup,
) -> None:
    journal = _rejournal(setup, retry_max_attempts=0, clock=_Clock())
    setup.client.fail_ticket_with = ServerError("upstream unavailable")
    projected = process(setup)
    assert (projected.state, projected.zammad_status) == (RequestState.FAILED, ZammadStatus.DONE)
    assert len(setup.client.notes) == 1

    setup.client.fail_list_tags_with = ServerError("upstream unavailable")
    skipped = process(setup)
    assert (skipped.state, skipped.zammad_status) == (RequestState.FAILED, ZammadStatus.PENDING)
    assert len(setup.client.notes) == 1

    setup.client.fail_list_tags_with = None
    scheduler = _scheduler(setup)
    asyncio.run(scheduler.run_maintenance())
    asyncio.run(scheduler.run_maintenance())

    assert [row.request_id for row in journal.pending_failure_reports()] == []
    assert len(setup.client.notes) == 2
    assert skipped.request_id in setup.client.notes[1][1]
    assert _row(setup, skipped.request_id).zammad_status is ZammadStatus.DONE


def test_transient_failures_note_once_then_retry_silently_until_the_final_note(
    setup: PipelineSetup,
) -> None:
    clock = _Clock()
    journal = _rejournal(setup, retry_max_attempts=2, clock=clock)
    setup.client.fail_ticket_with = ServerError("upstream unavailable")

    first = process(setup)
    assert (first.state, first.attempts) == (RequestState.WAITING, 1)
    assert setup.client.tags == {"pdf:processing"}
    [(_, retry_note)] = setup.client.notes
    assert "retrying automatically" in retry_note and "transient_error" in retry_note

    clock.advance()
    second = journal.lease_next()
    assert second is not None
    asyncio.run(setup.pipeline.process(second))
    assert _row(setup, first.request_id).state is RequestState.WAITING
    assert setup.client.tags == {"pdf:processing"}
    assert len(setup.client.notes) == 1

    clock.advance()
    third = journal.lease_next()
    assert third is not None
    setup.client.tags.add(TRIGGER)  # an agent reapplied the trigger meanwhile
    asyncio.run(setup.pipeline.process(third))

    row = _row(setup, first.request_id)
    assert (row.state, row.attempts, row.zammad_status) == (
        RequestState.FAILED,
        3,
        ZammadStatus.DONE,
    )
    assert setup.client.tags == {"pdf:error"}
    assert len(setup.client.notes) == 2
    assert "Failed after 3 attempts" in setup.client.notes[1][1]

    asyncio.run(_scheduler(setup).run_maintenance())
    assert len(setup.client.notes) == 2


@pytest.mark.parametrize(("trigger_login", "archive_user"), [("carol", "carol"), (None, "bob")])
def test_current_agent_is_the_user_who_triggered_the_request(
    tmp_path: Path, trigger_login: str | None, archive_user: str
) -> None:
    # The ticket was edited again after the trigger, by bob.
    client = FakeCaptureClient(
        tags=[TRIGGER],
        tickets=lambda _call: fixture_ticket(archive_user_mode="current_agent", updated_by="bob"),
    )
    built = build_setup(tmp_path, client)
    try:
        built.journal.admit(ticket_id=42, source=RequestSource.WEBHOOK, trigger_login=trigger_login)
        request = built.journal.lease_next()
        assert request is not None
        asyncio.run(built.pipeline.process(request))

        row = _row(built, request.request_id)
        assert row.state is RequestState.COMPLETE
        revision = revision_of(built, row)
        assert revision.archive_user == archive_user
        assert revision.key_prefix.startswith(f"it-authorization/{archive_user}/Customers/ACME/")
    finally:
        built.close()
