"""Verify the durable request journal's admission, leasing, recovery and safety rules."""

from __future__ import annotations

import sqlite3
import stat
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from chronikwerk.failures import PermanentError
from chronikwerk.operations.journal import (
    JournalOptions,
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


def test_admit_inserts_waiting_row(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        result = journal.admit(
            ticket_id=7, source=RequestSource.WEBHOOK, delivery_id="d1", evidence_json="{}"
        )
        assert result.outcome == "admitted"
        assert result.active_request_id is None
        row = journal.get(result.request_id or "")
    assert row is not None
    assert row.state is RequestState.WAITING
    assert (row.ticket_id, row.delivery_id, row.evidence_json) == (7, "d1", "{}")
    assert row.zammad_status is ZammadStatus.PENDING
    assert row.attempts == 0


def test_duplicate_delivery_creates_no_row(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        first = _admit(journal, 1, delivery_id="d1")
        result = journal.admit(ticket_id=2, source=RequestSource.WEBHOOK, delivery_id="d1")
        assert result.outcome == "duplicate_delivery"
        assert result.request_id is None
        assert result.active_request_id == first
        assert len(journal.list_recent()) == 1


def test_active_ticket_coalesces_new_request(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        first = _admit(journal, 1)
        result = journal.admit(ticket_id=1, source=RequestSource.RETRY)
        assert result.outcome == "coalesced"
        assert result.active_request_id == first
        row = journal.get(result.request_id or "")
        assert row is not None
        assert row.state is RequestState.COALESCED
        assert row.coalesced_into == first
        assert journal.count_by_state()[RequestState.WAITING] == 1


def test_coalesces_while_active_and_admits_after_completion(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        request_id = _to_verifying(journal, 1)
        assert journal.admit(ticket_id=1, source=RequestSource.WEBHOOK).outcome == "coalesced"
        journal.complete(request_id, revision_id="r1")
        # The coalesced request was promoted, so the ticket is still active.
        assert journal.admit(ticket_id=1, source=RequestSource.WEBHOOK).outcome == "coalesced"


def test_max_pending_rejects_when_full(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        _admit(journal, 1, max_pending=2)
        _admit(journal, 2, max_pending=2)
        full = journal.admit(ticket_id=3, source=RequestSource.WEBHOOK, max_pending=2)
        assert full.outcome == "full"
        assert full.request_id is None
        assert len(journal.list_recent()) == 2
        # Coalescing adds no waiting row, so it is still accepted when full.
        again = journal.admit(ticket_id=1, source=RequestSource.WEBHOOK, max_pending=2)
        assert again.outcome == "coalesced"


def test_lease_orders_oldest_first_and_respects_next_attempt(tmp_path: Path) -> None:
    clock = FakeClock()
    with _journal(tmp_path, clock, retry_base_delay_seconds=30.0) as journal:
        first = _admit(journal, 1)
        clock.advance(1)
        second = _admit(journal, 2)
        leased = journal.lease_next()
        assert leased is not None
        assert (leased.request_id, leased.state) == (first, RequestState.COLLECTING)
        journal.fail_transient(first, error_code="x", error_detail="y")
        later = journal.lease_next()
        assert later is not None
        assert later.request_id == second
        assert journal.lease_next() is None  # first still backing off
        clock.advance(60)
        again = journal.lease_next()
        assert again is not None
        assert again.request_id == first


def test_lease_twice_on_single_row_returns_none(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        _admit(journal)
        assert journal.lease_next() is not None
        assert journal.lease_next() is None


def test_backoff_doubles_and_caps_then_exhausts(tmp_path: Path) -> None:
    clock = FakeClock()
    with _journal(
        tmp_path,
        clock,
        retry_max_attempts=5,
        retry_base_delay_seconds=30.0,
        retry_max_delay_seconds=100.0,
    ) as journal:
        request_id = _admit(journal)
        delays: list[float] = []
        for _ in range(5):
            assert journal.lease_next() is not None
            row = journal.fail_transient(request_id, error_code="e", error_detail="d")
            assert row.state is RequestState.WAITING
            assert row.next_attempt_at is not None
            delays.append((row.next_attempt_at - clock.value).total_seconds())
            clock.advance(delays[-1])
        assert delays == [60.0, 100.0, 100.0, 100.0, 100.0]
        assert journal.lease_next() is not None
        final = journal.fail_transient(request_id, error_code="e", error_detail="boom")
        assert final.state is RequestState.FAILED
        assert final.attempts == 6
        assert final.next_attempt_at is None
        assert final.error_detail == "boom"


def test_complete_and_zammad_status_flow(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        request_id = _to_verifying(journal)
        journal.record_commit_step(request_id, "payload_written")
        journal.complete(request_id, revision_id="r1")
        row = journal.get(request_id)
        assert row is not None
        assert (row.state, row.revision_id, row.commit_step) == (
            RequestState.COMPLETE,
            "r1",
            "payload_written",
        )
        assert [r.request_id for r in journal.pending_zammad_updates()] == [request_id]
        journal.set_zammad_status(request_id, ZammadStatus.FAILED)
        assert len(journal.pending_zammad_updates()) == 1
        journal.set_zammad_status(request_id, ZammadStatus.DONE)
        assert journal.pending_zammad_updates() == []


def test_fail_permanent_marks_failed(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        _admit(journal)
        leased = journal.lease_next()
        assert leased is not None
        journal.fail_permanent(leased.request_id, error_code="bad", error_detail="nope")
        row = journal.get(leased.request_id)
        assert row is not None
        assert (row.state, row.error_code, row.error_detail) == (
            RequestState.FAILED,
            "bad",
            "nope",
        )
        assert journal.admit(ticket_id=1, source=RequestSource.RETRY).outcome == "admitted"


def test_restart_recovery_requeues_collecting_and_keeps_verifying(tmp_path: Path) -> None:
    clock = FakeClock()
    journal = _journal(tmp_path, clock)
    collecting = _admit(journal, 1)
    clock.advance(1)
    verifying = _admit(journal, 2)
    clock.advance(1)
    waiting = _admit(journal, 3)
    assert journal.lease_next() is not None
    assert journal.lease_next() is not None
    journal.mark_verifying(verifying, revision_id="rev-9", revision_number=9)
    journal.record_commit_step(verifying, "payload_written")
    journal.close()

    with _journal(tmp_path, clock) as reopened:
        requeued, resumable = reopened.recover_after_restart()
        assert [r.request_id for r in requeued] == [collecting]
        assert requeued[0].state is RequestState.WAITING
        assert requeued[0].attempts == 1
        assert [r.request_id for r in resumable] == [verifying]
        assert resumable[0].commit_step == "payload_written"
        assert (resumable[0].revision_id, resumable[0].revision_number) == ("rev-9", 9)
        assert resumable[0].attempts == 1  # the interrupted commit counts too
        counts = reopened.count_by_state()
        assert counts[RequestState.WAITING] == 2
        assert counts[RequestState.VERIFYING] == 1
        leased = reopened.lease_next()
        assert leased is not None
        assert leased.request_id == collecting
        next_leased = reopened.lease_next()
        assert next_leased is not None
        assert next_leased.request_id == waiting


def test_stuck_and_oldest_waiting_age(tmp_path: Path) -> None:
    clock = FakeClock()
    with _journal(tmp_path, clock) as journal:
        assert journal.oldest_waiting_age_seconds() is None
        _admit(journal, 1)
        clock.advance(90)
        assert journal.oldest_waiting_age_seconds() == pytest.approx(90.0)
        leased = journal.lease_next()
        assert leased is not None
        assert journal.stuck(older_than_seconds=60) == []
        clock.advance(61)
        assert [r.request_id for r in journal.stuck(older_than_seconds=60)] == [leased.request_id]


def test_count_by_state_includes_all_states(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        counts = journal.count_by_state()
    assert set(counts) == set(RequestState)
    assert sum(counts.values()) == 0


def test_list_recent_is_newest_first_and_limited(tmp_path: Path) -> None:
    clock = FakeClock()
    with _journal(tmp_path, clock) as journal:
        ids = []
        for ticket in (1, 2, 3):
            ids.append(_admit(journal, ticket))
            clock.advance(1)
        assert [r.request_id for r in journal.list_recent(limit=2)] == [ids[2], ids[1]]


def test_schema_version_and_wal(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.close()
    conn = sqlite3.connect(tmp_path / "journal.db")
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "journal.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=99")
    conn.close()
    with pytest.raises(PermanentError, match="schema version 99"):
        RequestJournal(JournalOptions(path=path))


def test_database_file_mode_is_private(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.close()
    assert stat.S_IMODE((tmp_path / "journal.db").stat().st_mode) == 0o600


def test_missing_parent_directory_raises_permanent_error(tmp_path: Path) -> None:
    with pytest.raises(PermanentError, match="directory does not exist"):
        RequestJournal(JournalOptions(path=tmp_path / "missing" / "journal.db"))


def test_naive_clock_is_rejected(tmp_path: Path) -> None:
    journal = RequestJournal(
        JournalOptions(path=tmp_path / "journal.db"), clock=lambda: datetime(2026, 1, 1)
    )
    with journal, pytest.raises(ValueError, match="timezone-aware"):
        journal.admit(ticket_id=1, source=RequestSource.WEBHOOK)


def test_illegal_transitions_raise(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        waiting = _admit(journal, 1)
        actions = (
            lambda: journal.complete(waiting, revision_id="r"),
            lambda: journal.mark_verifying(waiting, revision_id="r", revision_number=1),
            lambda: journal.record_commit_step(waiting, "s"),
            lambda: journal.fail_permanent(waiting, error_code="c", error_detail="d"),
            lambda: journal.fail_transient(waiting, error_code="c", error_detail="d"),
            lambda: journal.set_zammad_status(waiting, ZammadStatus.DONE),
            lambda: journal.mark_verifying("missing", revision_id="r", revision_number=1),
        )
        for action in actions:
            with pytest.raises(JournalStateError):
                action()
        row = journal.get(waiting)
        assert row is not None
        assert (row.state, row.attempts) == (RequestState.WAITING, 0)
        assert issubclass(JournalStateError, PermanentError)


def test_closed_journal_rejects_use(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.close()
    journal.close()
    with pytest.raises(JournalStateError, match="closed"):
        journal.admit(ticket_id=1, source=RequestSource.WEBHOOK)


def test_concurrent_admit_never_creates_two_active_rows(tmp_path: Path) -> None:
    results: list[str] = []
    with _journal(tmp_path) as journal:
        barrier = threading.Barrier(16)

        def worker() -> None:
            barrier.wait()
            results.append(journal.admit(ticket_id=1, source=RequestSource.WEBHOOK).outcome)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        counts = journal.count_by_state()
    assert results.count("admitted") == 1
    assert results.count("coalesced") == 15
    assert counts[RequestState.WAITING] == 1
    assert counts[RequestState.COALESCED] == 15


def test_concurrent_lease_never_double_leases(tmp_path: Path) -> None:
    leased_ids: list[str] = []
    with _journal(tmp_path) as journal:
        for ticket in range(10):
            _admit(journal, ticket)
        barrier = threading.Barrier(20)

        def worker() -> None:
            barrier.wait()
            row = journal.lease_next()
            if row is not None:
                leased_ids.append(row.request_id)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert len(leased_ids) == 10
    assert len(set(leased_ids)) == 10


def test_concurrent_duplicate_delivery_admits_once(tmp_path: Path) -> None:
    outcomes: list[str] = []
    with _journal(tmp_path) as journal:
        barrier = threading.Barrier(8)

        def worker(ticket: int) -> None:
            barrier.wait()
            result = journal.admit(
                ticket_id=ticket, source=RequestSource.WEBHOOK, delivery_id="same"
            )
            outcomes.append(result.outcome)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    assert outcomes.count("admitted") == 1
    assert outcomes.count("duplicate_delivery") == 7
