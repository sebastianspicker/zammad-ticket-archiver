"""Verify the non-blocking scheduler start and the isolated, periodic maintenance task."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from chronikwerk.operations.job import TicketJob
from chronikwerk.operations.journal import (
    JournalOptions,
    JournalRequest,
    RequestJournal,
    RequestSource,
    RequestState,
    ZammadStatus,
)
from chronikwerk.operations.scheduling import JournaledSchedulingService

Step = Callable[[JournalRequest], Awaitable[None]]


class FakeClock:
    """Mutable UTC clock for journal timestamps."""

    def __init__(self) -> None:
        self.value = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


class SpyJournal(RequestJournal):
    """Real journal that records whether maintenance and recovery reads ran on the loop."""

    def __init__(self, options: JournalOptions, **kwargs: object) -> None:
        super().__init__(options, **kwargs)  # type: ignore[arg-type]
        self.on_loop: dict[str, bool] = {}

    def _record(self, name: str) -> None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.on_loop[name] = False
        else:
            self.on_loop[name] = True

    def recover_after_restart(self) -> tuple[list[JournalRequest], list[JournalRequest]]:
        self._record("recover_after_restart")
        return super().recover_after_restart()

    def pending_zammad_updates(self) -> list[JournalRequest]:
        self._record("pending_zammad_updates")
        return super().pending_zammad_updates()

    def pending_failure_reports(self) -> list[JournalRequest]:
        self._record("pending_failure_reports")
        return super().pending_failure_reports()

    def purge_terminal(self, *, older_than_seconds: float) -> int:
        self._record("purge_terminal")
        return super().purge_terminal(older_than_seconds=older_than_seconds)


def _journal(
    tmp_path: Path, *, clock: FakeClock | None = None, retry_max_attempts: int = 5
) -> SpyJournal:
    """Open a spy journal inside ``tmp_path``."""
    options = JournalOptions(path=tmp_path / "journal.db", retry_max_attempts=retry_max_attempts)
    return SpyJournal(options, clock=clock or FakeClock())


class Processor:
    """Record maintenance calls; ``process`` and ``resume`` complete unless overridden."""

    def __init__(
        self,
        journal: RequestJournal,
        *,
        process: Step | None = None,
        resume: Step | None = None,
        publish: Step | None = None,
    ) -> None:
        self.journal = journal
        self.published: list[int] = []
        self.reported: list[int] = []
        self._process = process or self._complete
        self._resume = resume or self._complete
        self._publish = publish

    async def _complete(self, request: JournalRequest) -> None:
        self.journal.complete(request.request_id, revision_id=request.revision_id or "rev")

    async def process(self, request: JournalRequest) -> None:
        await self._process(request)

    async def resume(self, request: JournalRequest) -> None:
        await self._resume(request)

    async def publish_pending(self, request: JournalRequest) -> None:
        if self._publish is not None:
            await self._publish(request)
        self.published.append(request.ticket_id)
        self.journal.set_zammad_status(request.request_id, ZammadStatus.DONE)

    async def report_failure(self, request: JournalRequest) -> None:
        self.reported.append(request.ticket_id)
        self.journal.set_zammad_status(request.request_id, ZammadStatus.DONE)


def _service(
    journal: RequestJournal,
    processor: Processor,
    **kwargs: object,
) -> JournaledSchedulingService:
    """Build a scheduler with test-friendly timing; ``kwargs`` override the defaults."""
    options: dict[str, object] = {
        "max_pending": 10,
        "max_running": 1,
        "shutdown_timeout_seconds": 1.0,
        "poll_interval_seconds": 0.02,
        **kwargs,
    }
    return JournaledSchedulingService(
        journal=journal,
        processor=processor,
        **options,  # type: ignore[arg-type]
    )


async def _until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    """Yield to the loop until ``predicate`` holds or fail after ``timeout`` seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


def _to_state(journal: RequestJournal, ticket_id: int, state: RequestState) -> str:
    """Journal one request for ``ticket_id`` and drive it to ``state``."""
    journal.admit(ticket_id=ticket_id, source=RequestSource.WEBHOOK)
    leased = journal.lease_next()
    assert leased is not None
    if state is RequestState.VERIFYING:
        journal.mark_verifying(leased.request_id, revision_id="rev-v", revision_number=1)
    elif state is RequestState.COMPLETE:
        journal.complete(leased.request_id, revision_id=f"rev-{ticket_id}")
    elif state is RequestState.FAILED:
        journal.fail_permanent(leased.request_id, error_code="internal_error", error_detail="d")
    return leased.request_id


def test_start_returns_while_verifying_rows_are_still_resuming(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _journal(tmp_path) as journal:
            _to_state(journal, 1, RequestState.VERIFYING)
            release = asyncio.Event()
            resumed = asyncio.Event()

            async def resume(request: JournalRequest) -> None:
                resumed.set()
                await release.wait()
                journal.complete(request.request_id, revision_id="rev-v")

            service = _service(journal, Processor(journal, resume=resume))
            await asyncio.wait_for(service.start(), timeout=0.5)
            await asyncio.wait_for(resumed.wait(), timeout=2)
            assert await service.schedule(TicketJob(ticket_id=2))

            release.set()
            await _until(lambda: journal.count_by_state()[RequestState.COMPLETE] == 2)
            await service.aclose()
            assert journal.on_loop["recover_after_restart"] is False

    asyncio.run(scenario())


def test_aclose_cancels_a_resume_that_outlives_the_shutdown_timeout(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _journal(tmp_path) as journal:
            request_id = _to_state(journal, 1, RequestState.VERIFYING)
            resumed = asyncio.Event()

            async def hang(_request: JournalRequest) -> None:
                resumed.set()
                await asyncio.Event().wait()

            service = _service(
                journal, Processor(journal, resume=hang), shutdown_timeout_seconds=0.05
            )
            await service.start()
            await asyncio.wait_for(resumed.wait(), timeout=2)
            await asyncio.wait_for(service.aclose(), timeout=2)

            row = journal.get(request_id)
            assert row is not None and row.state is RequestState.VERIFYING

    asyncio.run(scenario())


def test_maintenance_at_start_publishes_reports_purges_and_runs_housekeeping(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        with _journal(tmp_path, clock=clock) as journal:
            old = _to_state(journal, 1, RequestState.COMPLETE)
            journal.set_zammad_status(old, ZammadStatus.DONE)
            clock.value += timedelta(days=100)
            _to_state(journal, 2, RequestState.COMPLETE)
            retry = _to_state(journal, 3, RequestState.COMPLETE)
            journal.set_zammad_status(retry, ZammadStatus.FAILED)
            _to_state(journal, 4, RequestState.FAILED)
            housekeeping: list[str] = []

            async def sweep() -> None:
                housekeeping.append("swept")

            processor = Processor(journal)
            service = _service(journal, processor, housekeeping=sweep)
            await service.start()
            await _until(lambda: housekeeping == ["swept"])
            await service.aclose()

            assert processor.published == [2, 3]
            assert processor.reported == [4]
            assert journal.get(old) is None
            assert sorted(row.ticket_id for row in journal.list_recent()) == [2, 3, 4]
            assert set(journal.on_loop) >= {
                "pending_zammad_updates",
                "pending_failure_reports",
                "purge_terminal",
            }
            assert not any(journal.on_loop.values())

    asyncio.run(scenario())


def test_each_maintenance_step_and_row_is_isolated(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _journal(tmp_path) as journal:
            for ticket_id in (1, 2):
                _to_state(journal, ticket_id, RequestState.COMPLETE)
            _to_state(journal, 3, RequestState.FAILED)

            async def flaky(request: JournalRequest) -> None:
                if request.ticket_id == 1:
                    raise RuntimeError("zammad down")

            def broken_purge(*, older_than_seconds: float) -> int:
                raise OSError("disk")

            calls: list[str] = []

            async def sweep() -> None:
                calls.append("swept")
                raise RuntimeError("sweep failed")

            processor = Processor(journal, publish=flaky)
            journal.purge_terminal = broken_purge  # type: ignore[method-assign]
            service = _service(journal, processor, housekeeping=sweep)

            await service.run_maintenance()
            await service.run_maintenance()

            assert processor.published == [2]
            assert [row.ticket_id for row in journal.pending_zammad_updates()] == [1]
            assert processor.reported == [3]
            assert calls == ["swept", "swept"]

    asyncio.run(scenario())


def test_maintenance_skips_rows_a_worker_is_still_handling(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _journal(tmp_path) as journal:
            completed = asyncio.Event()
            release = asyncio.Event()

            async def complete_then_wait(request: JournalRequest) -> None:
                journal.complete(request.request_id, revision_id="rev")
                completed.set()
                await release.wait()

            processor = Processor(journal, process=complete_then_wait)
            service = _service(journal, processor)
            await service.start()
            assert await service.schedule(TicketJob(ticket_id=1))
            await asyncio.wait_for(completed.wait(), timeout=2)

            await service.run_maintenance()
            assert processor.published == []

            release.set()
            await _until(lambda: journal.count_by_state()[RequestState.COLLECTING] == 0)
            await asyncio.sleep(0.05)
            await service.run_maintenance()
            assert processor.published == [1]
            await service.aclose()

    asyncio.run(scenario())


def test_maintenance_never_overlaps_with_itself(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _journal(tmp_path) as journal:
            active = 0
            peak = 0

            async def sweep() -> None:
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.05)
                active -= 1

            service = _service(journal, Processor(journal), housekeeping=sweep)
            await asyncio.gather(service.run_maintenance(), service.run_maintenance())

            assert peak == 1

    asyncio.run(scenario())


def test_maintenance_repeats_and_reports_safety_net_failures(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _journal(tmp_path, retry_max_attempts=0) as journal:
            runs: list[int] = []

            async def forget(_request: JournalRequest) -> None:
                return None

            async def sweep() -> None:
                runs.append(len(runs))

            processor = Processor(journal, process=forget)
            service = _service(
                journal, processor, housekeeping=sweep, maintenance_interval_seconds=0.02
            )
            await service.start()
            assert await service.schedule(TicketJob(ticket_id=7))

            await _until(lambda: processor.reported == [7] and len(runs) >= 2)
            await service.aclose()
            row = journal.list_recent()[0]
            assert (row.state, row.error_code, row.zammad_status) == (
                RequestState.FAILED,
                "internal_error",
                ZammadStatus.DONE,
            )

    asyncio.run(scenario())


def test_maintenance_skips_rows_that_changed_since_its_snapshot(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        with _journal(tmp_path, clock=clock) as journal:
            _to_state(journal, 1, RequestState.COMPLETE)
            second = _to_state(journal, 2, RequestState.COMPLETE)

            async def publish_elsewhere(request: JournalRequest) -> None:
                if request.ticket_id == 1:
                    # Another path publishes ticket 2 while maintenance is busy with ticket 1.
                    clock.value += timedelta(seconds=1)
                    journal.set_zammad_status(second, ZammadStatus.DONE)

            processor = Processor(journal, publish=publish_elsewhere)
            await _service(journal, processor).run_maintenance()

            assert processor.published == [1]

    asyncio.run(scenario())


async def _orphaned(
    journal: RequestJournal, processor: Processor, monkeypatch: pytest.MonkeyPatch
) -> JournaledSchedulingService:
    """Start a scheduler and leave ticket 1 in flight: processing and its journal write fail."""
    failed_writes: list[str] = []

    def disk_full(request_id: str, **_kwargs: object) -> JournalRequest:
        failed_writes.append(request_id)
        raise sqlite3.OperationalError("database or disk is full")

    service = _service(journal, processor, maintenance_interval_seconds=60.0)
    await service.start()
    monkeypatch.setattr(journal, "fail_transient", disk_full)
    assert await service.schedule(TicketJob(ticket_id=1))
    await _until(lambda: bool(failed_writes))
    await asyncio.sleep(0.05)
    monkeypatch.undo()  # the disk has space again
    return service


def test_maintenance_requeues_a_collecting_row_left_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        with _journal(tmp_path, clock=clock) as journal:

            async def capture_fails(_request: JournalRequest) -> None:
                raise RuntimeError("capture failed")

            processor = Processor(journal, process=capture_fails)
            service = await _orphaned(journal, processor, monkeypatch)

            await service.run_maintenance()  # not older than one interval yet
            assert journal.count_by_state()[RequestState.COLLECTING] == 1

            clock.value += timedelta(seconds=120)
            await service.run_maintenance()
            [row] = journal.list_recent()
            assert (row.state, row.error_code, row.attempts, row.revision_id) == (
                RequestState.WAITING,
                "internal_error",
                1,
                None,
            )
            await service.aclose()

    asyncio.run(scenario())


def test_maintenance_resumes_a_verifying_row_left_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        with _journal(tmp_path, clock=clock) as journal:
            resumed: list[int] = []

            async def commit_then_fail(request: JournalRequest) -> None:
                journal.mark_verifying(request.request_id, revision_id="rev-o", revision_number=1)
                raise RuntimeError("store unreachable")

            async def resume(request: JournalRequest) -> None:
                resumed.append(request.ticket_id)
                journal.complete(request.request_id, revision_id="rev-o")

            processor = Processor(journal, process=commit_then_fail, resume=resume)
            service = await _orphaned(journal, processor, monkeypatch)
            assert journal.count_by_state()[RequestState.VERIFYING] == 1

            clock.value += timedelta(seconds=120)
            await service.run_maintenance()
            assert resumed == [1]
            [row] = journal.list_recent()
            assert (row.state, row.revision_id) == (RequestState.COMPLETE, "rev-o")
            await service.aclose()

    asyncio.run(scenario())


def test_orphan_recovery_leaves_rows_that_startup_or_a_worker_still_owns(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = FakeClock()
        with _journal(tmp_path, clock=clock) as journal:
            for ticket_id in (1, 2):
                _to_state(journal, ticket_id, RequestState.VERIFYING)
            release = asyncio.Event()
            started = asyncio.Event()

            async def wait_for_release(request: JournalRequest) -> None:
                started.set()
                await release.wait()
                journal.complete(request.request_id, revision_id=request.revision_id or "rev")

            processor = Processor(journal, process=wait_for_release, resume=wait_for_release)
            service = _service(journal, processor, maintenance_interval_seconds=60.0)
            await service.start()
            await asyncio.wait_for(started.wait(), timeout=2)
            clock.value += timedelta(seconds=120)
            await service.run_maintenance()  # startup still resumes: no row is an orphan
            assert journal.count_by_state()[RequestState.VERIFYING] == 2

            release.set()
            await _until(lambda: journal.count_by_state()[RequestState.COMPLETE] == 2)
            release.clear()
            started.clear()
            assert await service.schedule(TicketJob(ticket_id=3))
            await asyncio.wait_for(started.wait(), timeout=2)
            clock.value += timedelta(seconds=120)
            await service.run_maintenance()  # a worker holds the row
            assert journal.count_by_state()[RequestState.COLLECTING] == 1

            release.set()
            await _until(lambda: journal.count_by_state()[RequestState.COMPLETE] == 3)
            await service.aclose()

    asyncio.run(scenario())
