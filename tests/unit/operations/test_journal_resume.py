"""Verify the journal rules for trigger logins, in-process resume and pending failure reports."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from chronikwerk.operations.journal import (
    MAX_TRIGGER_LOGIN_CHARS,
    AdmitItem,
    JournalOptions,
    JournalRequest,
    RequestJournal,
    RequestSource,
    RequestState,
    ZammadStatus,
)

_START = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)


class FakeClock:
    """Mutable UTC clock for deterministic journal timing."""

    def __init__(self) -> None:
        self.value = _START

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self.value += timedelta(seconds=seconds)


def _journal(tmp_path: Path, clock: FakeClock, *, retry_max_attempts: int = 5) -> RequestJournal:
    """Open a journal inside ``tmp_path``."""
    options = JournalOptions(path=tmp_path / "journal.db", retry_max_attempts=retry_max_attempts)
    return RequestJournal(options, clock=clock)


def _lease(journal: RequestJournal, ticket_id: int = 1) -> JournalRequest:
    """Admit one webhook request for ``ticket_id`` and lease it."""
    journal.admit(ticket_id=ticket_id, source=RequestSource.WEBHOOK)
    leased = journal.lease_next()
    assert leased is not None
    return leased


def _verifying(journal: RequestJournal, *, context_json: str | None = '{"k": 1}') -> str:
    """Lease a request and move it to VERIFYING with a revision and commit step."""
    request_id = _lease(journal).request_id
    journal.mark_verifying(
        request_id, revision_id="rev-1", revision_number=3, context_json=context_json
    )
    journal.record_commit_step(request_id, "seal_uploaded")
    return request_id


def _row(journal: RequestJournal, request_id: str) -> JournalRequest:
    """Return an existing row."""
    row = journal.get(request_id)
    assert row is not None
    return row


def _commit_fields(row: JournalRequest) -> tuple[object, ...]:
    """Return the fields an in-process resume depends on."""
    return (row.revision_id, row.revision_number, row.commit_step, row.context_json)


def test_trigger_login_is_stored_and_capped(tmp_path: Path) -> None:
    with _journal(tmp_path, FakeClock()) as journal:
        single = journal.admit(ticket_id=1, source=RequestSource.WEBHOOK, trigger_login="agent")
        batch = journal.admit_many(
            [
                AdmitItem(2, RequestSource.WEBHOOK, trigger_login="x" * 300),
                AdmitItem(3, RequestSource.WEBHOOK, trigger_login=""),
                AdmitItem(4, RequestSource.WEBHOOK),
            ],
            max_pending=None,
        )
        assert single.request_id is not None and batch is not None

        logins = [
            _row(journal, result.request_id).trigger_login
            for result in (single, *batch)
            if result.request_id is not None
        ]
        assert logins == ["agent", "x" * MAX_TRIGGER_LOGIN_CHARS, None, None]


def test_retry_max_attempts_is_exposed(tmp_path: Path) -> None:
    with _journal(tmp_path, FakeClock(), retry_max_attempts=7) as journal:
        assert journal.retry_max_attempts == 7


def test_keep_revision_returns_a_verifying_row_with_its_commit_context(tmp_path: Path) -> None:
    clock = FakeClock()
    with _journal(tmp_path, clock) as journal:
        request_id = _verifying(journal)

        row = journal.fail_transient(
            request_id, error_code="store_write_failed", error_detail="503", keep_revision=True
        )

        assert (row.state, row.attempts) == (RequestState.WAITING, 1)
        assert row.next_attempt_at == _START + timedelta(seconds=60)
        assert _commit_fields(row) == ("rev-1", 3, "seal_uploaded", '{"k": 1}')
        assert journal.lease_next() is None  # still backing off
        clock.advance(60)
        leased = journal.lease_next()
        assert leased is not None and leased.state is RequestState.COLLECTING
        assert _commit_fields(leased) == ("rev-1", 3, "seal_uploaded", '{"k": 1}')


def test_keep_revision_without_context_or_while_collecting_clears_the_commit(
    tmp_path: Path,
) -> None:
    with _journal(tmp_path, FakeClock()) as journal:
        no_context = _verifying(journal, context_json=None)
        collecting = _lease(journal, ticket_id=2).request_id

        for request_id in (no_context, collecting):
            row = journal.fail_transient(
                request_id, error_code="e", error_detail="d", keep_revision=True
            )
            assert row.state is RequestState.WAITING
            assert _commit_fields(row) == (None, None, None, None)


def test_keep_revision_still_fails_after_the_last_attempt(tmp_path: Path) -> None:
    with _journal(tmp_path, FakeClock(), retry_max_attempts=0) as journal:
        request_id = _verifying(journal)

        row = journal.fail_transient(
            request_id, error_code="e", error_detail="d", keep_revision=True
        )

        assert (row.state, row.zammad_status) == (RequestState.FAILED, ZammadStatus.PENDING)


def test_failures_recorded_outside_the_pipeline_await_a_report(tmp_path: Path) -> None:
    with _journal(tmp_path, FakeClock(), retry_max_attempts=0) as journal:
        exhausted = _lease(journal, ticket_id=1).request_id
        journal.fail_transient(exhausted, error_code="internal_error", error_detail="boom")
        reported = _lease(journal, ticket_id=2).request_id
        journal.fail_permanent(reported, error_code="seal_failed", error_detail="d")
        journal.set_zammad_status(reported, ZammadStatus.DONE)
        complete = _lease(journal, ticket_id=3).request_id
        journal.complete(complete, revision_id="rev-3")

        assert [row.request_id for row in journal.pending_failure_reports()] == [exhausted]
        assert [row.request_id for row in journal.pending_zammad_updates()] == [complete]


def test_restart_counts_interrupted_verifying_rows_and_fails_them_eventually(
    tmp_path: Path,
) -> None:
    clock = FakeClock()
    with _journal(tmp_path, clock, retry_max_attempts=1) as journal:
        request_id = _verifying(journal)

    with _journal(tmp_path, clock, retry_max_attempts=1) as journal:
        _requeued, verifying = journal.recover_after_restart()
        assert [(row.request_id, row.attempts) for row in verifying] == [(request_id, 1)]
        assert verifying[0].state is RequestState.VERIFYING
        assert _commit_fields(verifying[0]) == ("rev-1", 3, "seal_uploaded", '{"k": 1}')

    with _journal(tmp_path, clock, retry_max_attempts=1) as journal:
        _requeued, verifying = journal.recover_after_restart()
        assert verifying == []
        row = _row(journal, request_id)
        assert (row.state, row.error_code, row.attempts, row.zammad_status) == (
            RequestState.FAILED,
            "interrupted_repeatedly",
            2,
            ZammadStatus.PENDING,
        )
        assert [report.request_id for report in journal.pending_failure_reports()] == [request_id]


def test_history_page_filters_not_triggered_failures(tmp_path: Path) -> None:
    with _journal(tmp_path, FakeClock()) as journal:
        skipped = _lease(journal, ticket_id=1).request_id
        journal.fail_permanent(skipped, error_code="not_triggered", error_detail="no tag")
        failed = _lease(journal, ticket_id=2).request_id
        journal.fail_permanent(failed, error_code="seal_failed", error_detail="d")
        waiting = journal.admit(ticket_id=3, source=RequestSource.WEBHOOK).request_id

        def ids(not_triggered: bool | None) -> list[str]:
            page = journal.list_page(10, None, None, None, not_triggered=not_triggered)
            return [row.request_id for _rowid, row in page]

        assert ids(None) == [waiting, failed, skipped]
        assert ids(True) == [waiting, skipped]
        assert ids(False) == [waiting, failed]
