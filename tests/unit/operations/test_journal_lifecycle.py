"""Verify the request journal's coalescing, retry, recovery, bounds and file-safety rules."""

from __future__ import annotations

import os
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from chronikwerk.failures import PermanentError
from chronikwerk.operations.journal import (
    JournalOptions,
    JournalRequest,
    JournalStateError,
    RequestJournal,
    RequestSource,
    RequestState,
    ZammadStatus,
)


class FakeClock:
    """Mutable UTC clock for deterministic journal timing."""

    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: float) -> None:
        """Move the clock forward."""
        self.value += timedelta(seconds=seconds)


def _journal(
    tmp_path: Path,
    clock: FakeClock | None = None,
    *,
    retry_max_attempts: int = 5,
    retry_max_delay_seconds: float = 3600.0,
    retry_base_delay_seconds: float = 30.0,
) -> RequestJournal:
    """Open a journal on a fresh database inside ``tmp_path``."""
    options = JournalOptions(
        path=tmp_path / "journal.db",
        retry_max_attempts=retry_max_attempts,
        retry_max_delay_seconds=retry_max_delay_seconds,
        retry_base_delay_seconds=retry_base_delay_seconds,
    )
    return RequestJournal(options, clock=clock or FakeClock())


def _admit(
    journal: RequestJournal,
    ticket_id: int = 1,
    *,
    delivery_id: str | None = None,
    max_pending: int | None = None,
) -> str:
    """Admit one webhook request and return its id."""
    result = journal.admit(
        ticket_id=ticket_id,
        source=RequestSource.WEBHOOK,
        delivery_id=delivery_id,
        max_pending=max_pending,
    )
    assert result.request_id is not None
    return result.request_id


def _to_verifying(journal: RequestJournal, ticket_id: int = 1) -> str:
    """Admit and lease a request, then move it to VERIFYING."""
    _admit(journal, ticket_id)
    leased = journal.lease_next()
    assert leased is not None
    journal.mark_verifying(leased.request_id, revision_id="r1", revision_number=1)
    return leased.request_id


def _lease_one(journal: RequestJournal) -> JournalRequest:
    """Lease the next request, which must exist."""
    leased = journal.lease_next()
    assert leased is not None
    return leased


def _row(journal: RequestJournal, request_id: str) -> JournalRequest:
    """Return an existing row."""
    row = journal.get(request_id)
    assert row is not None
    return row


def _sql(journal: RequestJournal, sql: str, params: tuple[object, ...] = ()) -> None:
    """Run raw SQL against the journal's connection (test-only state setup)."""
    with journal._lock:
        assert journal._conn is not None
        journal._conn.execute(sql, params)


def test_commit_step_is_cleared_after_transient_failure(tmp_path: Path) -> None:
    clock = FakeClock()
    with _journal(tmp_path, clock) as journal:
        request_id = _to_verifying(journal)
        journal.record_commit_step(request_id, "payload_written")
        row = journal.fail_transient(request_id, error_code="e", error_detail="d")
        assert (row.state, row.commit_step, row.revision_id, row.revision_number) == (
            RequestState.WAITING,
            None,
            None,
            None,
        )
        clock.advance(60)
        leased = _lease_one(journal)
        assert (leased.commit_step, leased.revision_id, leased.revision_number) == (
            None,
            None,
            None,
        )


def test_lease_keeps_the_commit_fields_of_a_waiting_row(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        request_id = _admit(journal)
        _sql(journal, "UPDATE requests SET commit_step = 'kept', revision_id = 'rev'")
        leased = _lease_one(journal)
        assert (leased.request_id, leased.commit_step, leased.revision_id) == (
            request_id,
            "kept",
            "rev",
        )


def test_restart_requeue_clears_commit_step(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        request_id = _admit(journal)
        _lease_one(journal)
        _sql(journal, "UPDATE requests SET commit_step = 'stale'")
        requeued, _ = journal.recover_after_restart()
        assert [r.request_id for r in requeued] == [request_id]
        assert requeued[0].commit_step is None


def test_complete_requires_matching_revision_while_verifying(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        request_id = _to_verifying(journal)
        with pytest.raises(JournalStateError, match="verifies revision"):
            journal.complete(request_id, revision_id="other")
        journal.complete(request_id, revision_id="r1")
        row = _row(journal, request_id)
        assert (row.state, row.revision_id, row.revision_number) == (
            RequestState.COMPLETE,
            "r1",
            1,
        )


def test_complete_from_collecting_short_circuit(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        request_id = _admit(journal)
        _lease_one(journal)
        journal.complete(request_id, revision_id="existing")
        row = _row(journal, request_id)
        assert (row.state, row.revision_id, row.revision_number) == (
            RequestState.COMPLETE,
            "existing",
            None,
        )
        assert row.zammad_status is ZammadStatus.PENDING


def test_coalesced_row_is_promoted_after_completion(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        first = _to_verifying(journal)
        second = journal.admit(ticket_id=1, source=RequestSource.RETRY).request_id or ""
        journal.complete(first, revision_id="r1")
        row = _row(journal, second)
        assert (row.state, row.coalesced_into, row.attempts) == (RequestState.WAITING, None, 0)
        assert _lease_one(journal).request_id == second


def test_coalesced_row_is_promoted_after_exhausted_and_permanent_failure(tmp_path: Path) -> None:
    with _journal(tmp_path, retry_max_attempts=0) as journal:
        first = _admit(journal, 1)
        queued = journal.admit(ticket_id=1, source=RequestSource.RETRY).request_id or ""
        _lease_one(journal)
        row = journal.fail_transient(first, error_code="e", error_detail="d")
        assert row.state is RequestState.FAILED
        assert _row(journal, queued).state is RequestState.WAITING
        later = journal.admit(ticket_id=1, source=RequestSource.RETRY).request_id or ""
        assert _row(journal, later).state is RequestState.COALESCED
        _lease_one(journal)
        journal.fail_permanent(queued, error_code="e", error_detail="d")
        assert _row(journal, later).state is RequestState.WAITING


def test_evidence_rows_are_promoted_one_by_one(tmp_path: Path) -> None:
    clock = FakeClock()
    with _journal(tmp_path, clock) as journal:
        first = _admit(journal)
        ids = []
        for payload in ('{"a": 1}', None, '{"b": 2}', None):
            clock.advance(1)
            result = journal.admit(
                ticket_id=1, source=RequestSource.MAIL_EVIDENCE, evidence_json=payload
            )
            ids.append(result.request_id or "")
        evidence_a, plain_a, evidence_b, plain_b = ids
        _lease_one(journal)
        journal.mark_verifying(first, revision_id="r1", revision_number=1)
        journal.complete(first, revision_id="r1")
        assert _row(journal, evidence_a).state is RequestState.WAITING
        assert _row(journal, evidence_b).state is RequestState.COALESCED
        assert _row(journal, evidence_b).coalesced_into == evidence_a
        assert _row(journal, plain_a).coalesced_into == evidence_a

        _lease_one(journal)
        journal.mark_verifying(evidence_a, revision_id="r2", revision_number=2)
        journal.complete(evidence_a, revision_id="r2")
        for plain in (plain_a, plain_b):
            done = _row(journal, plain)
            assert (done.state, done.revision_id, done.revision_number) == (
                RequestState.COMPLETE,
                "r2",
                2,
            )
            assert done.zammad_status is ZammadStatus.DONE
        assert _row(journal, evidence_b).state is RequestState.WAITING


def test_plain_duplicates_collapse_into_one_capture(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        first = _to_verifying(journal)
        duplicates = [
            journal.admit(ticket_id=1, source=RequestSource.WEBHOOK).request_id or ""
            for _ in range(3)
        ]
        journal.complete(first, revision_id="r1")
        promoted = _lease_one(journal)
        assert promoted.request_id == duplicates[0]
        journal.mark_verifying(promoted.request_id, revision_id="r2", revision_number=2)
        journal.complete(promoted.request_id, revision_id="r2")
        assert journal.lease_next() is None
        counts = journal.count_by_state()
        assert counts[RequestState.COMPLETE] == 4
        assert counts[RequestState.COALESCED] == 0
        assert [_row(journal, d).revision_id for d in duplicates] == ["r2", "r2", "r2"]


def test_coalesced_rows_are_capped_per_ticket(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        active = _admit(journal)
        for _ in range(20):
            assert journal.admit(ticket_id=1, source=RequestSource.WEBHOOK).request_id
        capped = journal.admit(ticket_id=1, source=RequestSource.WEBHOOK)
        assert (capped.outcome, capped.request_id, capped.active_request_id) == (
            "coalesced",
            None,
            active,
        )
        assert journal.count_by_state()[RequestState.COALESCED] == 20
        assert journal.admit(ticket_id=2, source=RequestSource.WEBHOOK).outcome == "admitted"


def test_evidence_length_is_bounded(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        journal.admit(ticket_id=1, source=RequestSource.MAIL_EVIDENCE, evidence_json="x" * 65536)
        with pytest.raises(ValueError, match="evidence_json"):
            journal.admit(
                ticket_id=2, source=RequestSource.MAIL_EVIDENCE, evidence_json="x" * 65537
            )
        assert len(journal.list_recent()) == 1


def test_error_detail_is_truncated(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        _admit(journal)
        leased = _lease_one(journal)
        journal.fail_permanent(leased.request_id, error_code="e", error_detail="d" * 10000)
        assert len(_row(journal, leased.request_id).error_detail or "") == 4096


def test_purge_terminal_deletes_only_old_finished_rows(tmp_path: Path) -> None:
    clock = FakeClock()
    with _journal(tmp_path, clock) as journal:
        done = _to_verifying(journal, 1)
        journal.complete(done, revision_id="r1")
        failed = _admit(journal, 2)
        _lease_one(journal)
        journal.fail_permanent(failed, error_code="e", error_detail="d")
        assert journal.purge_terminal(older_than_seconds=3600) == 0
        clock.advance(7200)
        recent = _admit(journal, 3)
        assert journal.purge_terminal(older_than_seconds=3600) == 2
        assert [row.request_id for row in journal.list_recent()] == [recent]


def test_purge_removes_coalesced_rows_of_terminal_target(tmp_path: Path) -> None:
    clock = FakeClock()
    with _journal(tmp_path, clock) as journal:
        first = _admit(journal)
        queued = journal.admit(ticket_id=1, source=RequestSource.WEBHOOK).request_id or ""
        _sql(journal, "UPDATE requests SET state = 'failed' WHERE request_id = ?", (first,))
        clock.advance(7200)
        assert journal.purge_terminal(older_than_seconds=3600) == 2
        assert journal.get(queued) is None


def test_recovery_counts_interruptions_and_fails_crash_loops(tmp_path: Path) -> None:
    with _journal(tmp_path, retry_max_attempts=2) as journal:
        request_id = _admit(journal)
        queued = journal.admit(ticket_id=1, source=RequestSource.RETRY).request_id or ""
        for expected in (1, 2):
            _lease_one(journal)
            journal.recover_after_restart()
            row = _row(journal, request_id)
            assert (row.state, row.attempts) == (RequestState.WAITING, expected)
        _lease_one(journal)
        requeued, _ = journal.recover_after_restart()
        assert requeued == []
        row = _row(journal, request_id)
        assert (row.state, row.error_code) == (RequestState.FAILED, "interrupted_repeatedly")
        assert _row(journal, queued).state is RequestState.WAITING


def test_commit_failure_rolls_back_and_reraises_original(tmp_path: Path) -> None:
    class FailingCommit:
        """Connection stand-in whose COMMIT and ROLLBACK both fail."""

        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn
            self.rollbacks = 0

        def execute(self, sql: str, *args: object) -> sqlite3.Cursor:
            if sql == "COMMIT":
                raise sqlite3.OperationalError("disk I/O error")
            if sql == "ROLLBACK":
                self.rollbacks += 1
                raise sqlite3.OperationalError("cannot rollback")
            return self._conn.execute(sql, *args)  # type: ignore[arg-type]

    journal = _journal(tmp_path)
    real = journal._conn
    assert real is not None
    fake = FailingCommit(real)
    journal._conn = fake  # type: ignore[assignment]
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        journal.admit(ticket_id=1, source=RequestSource.WEBHOOK)
    assert fake.rollbacks == 1
    journal._conn = real
    real.execute("ROLLBACK")
    assert journal.list_recent() == []
    journal.close()


def test_corrupt_database_is_a_permanent_error(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    path.write_bytes(b"this is not a sqlite database" * 100)
    with pytest.raises(PermanentError, match="Cannot use journal database"):
        RequestJournal(JournalOptions(path=path))


def test_locked_database_is_a_permanent_error(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    _journal(tmp_path).close()
    blocker = sqlite3.connect(path, isolation_level=None)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(PermanentError, match="journal database"):
            RequestJournal(JournalOptions(path=path))
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()


def test_existing_database_file_is_made_private(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    path.touch()
    path.chmod(0o644)
    _journal(tmp_path).close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_symlinked_database_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere.db"
    target.touch()
    link = tmp_path / "journal.db"
    link.symlink_to(target)
    with pytest.raises(PermanentError, match="Cannot open journal database"):
        RequestJournal(JournalOptions(path=link))


def test_world_writable_parent_without_sticky_bit_is_refused(tmp_path: Path) -> None:
    parent = tmp_path / "shared"
    parent.mkdir()
    parent.chmod(0o777)
    with pytest.raises(PermanentError, match="world-writable"):
        RequestJournal(JournalOptions(path=parent / "journal.db"))
    parent.chmod(0o777 | stat.S_ISVTX)
    RequestJournal(JournalOptions(path=parent / "journal.db")).close()
    assert os.path.exists(parent / "journal.db")
