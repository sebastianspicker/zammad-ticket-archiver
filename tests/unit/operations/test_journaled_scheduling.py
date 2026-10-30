"""Verify journal-backed admission, the bounded worker pool, recovery and shutdown."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from chronikwerk.operations.job import TicketJob
from chronikwerk.operations.journal import (
    JournalOptions,
    JournalRequest,
    RequestJournal,
    RequestSource,
    RequestState,
)
from chronikwerk.operations.scheduling import JournaledSchedulingService

Step = Callable[[JournalRequest], Awaitable[None]]


def _journal(tmp_path: Path, *, retry_base_delay_seconds: float = 30.0) -> RequestJournal:
    """Open a real journal inside ``tmp_path`` with the real clock."""
    return RequestJournal(
        JournalOptions(
            path=tmp_path / "journal.db",
            retry_base_delay_seconds=retry_base_delay_seconds,
        )
    )


class FakeProcessor:
    """Record calls; complete each request unless a test supplies other behaviour."""

    def __init__(
        self,
        journal: RequestJournal,
        *,
        process: Step | None = None,
        resume: Step | None = None,
    ) -> None:
        self.journal = journal
        self.calls: list[tuple[str, int]] = []
        self.maintenance: list[tuple[str, int]] = []
        self._process = process or self.complete
        self._resume = resume or self.complete

    async def complete(self, request: JournalRequest) -> None:
        """Finish the request with a revision named after it."""
        self.journal.complete(
            request.request_id, revision_id=request.revision_id or f"rev-{request.request_id}"
        )

    async def process(self, request: JournalRequest) -> None:
        """Record and run the configured process behaviour."""
        self.calls.append(("process", request.ticket_id))
        await self._process(request)

    async def resume(self, request: JournalRequest) -> None:
        """Record and run the configured resume behaviour."""
        self.calls.append(("resume", request.ticket_id))
        await self._resume(request)

    async def publish_pending(self, request: JournalRequest) -> None:
        """Record a maintenance retry of the Zammad update."""
        self.maintenance.append(("publish_pending", request.ticket_id))

    async def report_failure(self, request: JournalRequest) -> None:
        """Record a maintenance failure report."""
        self.maintenance.append(("report_failure", request.ticket_id))


def _service(
    journal: RequestJournal,
    processor: FakeProcessor,
    *,
    max_pending: int = 10,
    max_running: int = 1,
    shutdown_timeout_seconds: float = 1.0,
    poll_interval_seconds: float = 0.05,
) -> JournaledSchedulingService:
    """Build a journaled scheduler with test-friendly timing."""
    return JournaledSchedulingService(
        journal=journal,
        processor=processor,
        max_pending=max_pending,
        max_running=max_running,
        shutdown_timeout_seconds=shutdown_timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def _job(ticket_id: int, delivery_id: str | None = None) -> TicketJob:
    """Build one webhook job."""
    return TicketJob(ticket_id=ticket_id, delivery_id=delivery_id)


def _states(journal: RequestJournal) -> dict[RequestState, int]:
    """Return non-zero row counts by state."""
    return {state: count for state, count in journal.count_by_state().items() if count}


async def _until(predicate: Callable[[], bool], timeout: float = 3.0) -> None:
    """Yield to the loop until ``predicate`` holds or fail after ``timeout`` seconds."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("condition not reached in time")
        await asyncio.sleep(0.005)


def test_schedule_reports_admission_outcomes(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _journal(tmp_path) as journal:
            service = _service(journal, FakeProcessor(journal), max_pending=1)

            assert await service.schedule(_job(1, "d-1")) is True
            assert await service.schedule(_job(1, "d-1")) is True  # duplicate delivery
            assert await service.schedule(_job(1)) is True  # coalesced behind the waiting row
            assert await service.schedule(_job(2)) is False  # max_pending waiting rows
            assert await service.schedule_retry(ticket_id=1, request_id="r-1") is True

            assert _states(journal) == {RequestState.WAITING: 1, RequestState.COALESCED: 2}
            sources = {row.source for row in journal.list_recent()}
            assert sources == {RequestSource.WEBHOOK, RequestSource.RETRY}
            assert (service.admission.pending, service.admission.running) == (1, 0)
            assert service.admission.closing is False

    asyncio.run(scenario())


def test_batch_is_admitted_all_or_nothing(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _journal(tmp_path) as journal:
            service = _service(journal, FakeProcessor(journal), max_pending=2)

            assert await service.schedule_batch([_job(1), _job(2), _job(3)]) is False
            assert journal.list_recent() == []

            assert await service.schedule_batch([_job(1), _job(2), _job(1)]) is True
            assert _states(journal) == {RequestState.WAITING: 2, RequestState.COALESCED: 1}

    asyncio.run(scenario())


def test_closed_service_rejects_every_admission(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _journal(tmp_path) as journal:
            service = _service(journal, FakeProcessor(journal))
            await service.start()
            await service.aclose()

            assert service.accepting is False
            assert service.admission.closing is True
            assert await service.schedule(_job(1)) is False
            assert await service.schedule_batch([_job(1)]) is False
            assert await service.schedule_retry(ticket_id=1, request_id=None) is False
            assert journal.list_recent() == []

    asyncio.run(scenario())


def test_workers_respect_max_running(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _journal(tmp_path) as journal:
            release = asyncio.Event()
            active: list[int] = []
            peak = 0

            async def blocking(request: JournalRequest) -> None:
                nonlocal peak
                active.append(request.ticket_id)
                peak = max(peak, len(active))
                await release.wait()
                active.remove(request.ticket_id)
                await processor.complete(request)

            processor = FakeProcessor(journal, process=blocking)
            service = _service(journal, processor, max_running=2)
            await service.start()
            assert await service.schedule_batch([_job(index) for index in range(1, 6)])

            await _until(lambda: len(active) == 2)
            await asyncio.sleep(0.1)
            assert peak == 2
            assert (service.admission.pending, service.admission.running) == (3, 2)

            release.set()
            await _until(lambda: _states(journal) == {RequestState.COMPLETE: 5})
            assert peak == 2
            await service.aclose()

    asyncio.run(scenario())


def test_backed_off_request_wakes_up_without_new_admissions(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _journal(tmp_path, retry_base_delay_seconds=0.01) as journal:

            async def fail_once(request: JournalRequest) -> None:
                if request.attempts == 0:
                    journal.fail_transient(
                        request.request_id, error_code="zammad_unavailable", error_detail="503"
                    )
                else:
                    await processor.complete(request)

            processor = FakeProcessor(journal, process=fail_once)
            service = _service(journal, processor, poll_interval_seconds=0.02)
            await service.start()
            assert await service.schedule(_job(1))

            await _until(lambda: _states(journal) == {RequestState.COMPLETE: 1})
            assert processor.calls == [("process", 1), ("process", 1)]
            await service.aclose()

    asyncio.run(scenario())


def test_processor_exception_records_scrubbed_transient_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _journal(tmp_path) as journal:

            async def explode(_request: JournalRequest) -> None:
                raise RuntimeError("upstream said api_token=supersecret")

            service = _service(journal, FakeProcessor(journal, process=explode))
            await service.start()
            assert await service.schedule(_job(1))

            await _until(lambda: journal.list_recent()[0].attempts == 1)
            await service.aclose()
            row = journal.list_recent()[0]
            assert row.state is RequestState.WAITING
            assert row.error_code == "internal_error"
            assert row.error_detail is not None
            assert row.error_detail.startswith("RuntimeError: ")
            assert "supersecret" not in row.error_detail

    asyncio.run(scenario())


def test_processor_leaving_row_in_flight_records_internal_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _journal(tmp_path) as journal:

            async def forget(_request: JournalRequest) -> None:
                return None

            service = _service(journal, FakeProcessor(journal, process=forget))
            await service.start()
            assert await service.schedule(_job(1))

            await _until(lambda: journal.list_recent()[0].attempts == 1)
            await service.aclose()
            row = journal.list_recent()[0]
            assert (row.state, row.error_code) == (RequestState.WAITING, "internal_error")
            assert "collecting" in (row.error_detail or "")

    asyncio.run(scenario())


def test_restart_resumes_verifying_rows_before_leasing(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _journal(tmp_path) as journal:
            for ticket_id in (1, 2):
                journal.admit(ticket_id=ticket_id, source=RequestSource.WEBHOOK)
            first, second = journal.lease_next(), journal.lease_next()
            assert first is not None and second is not None
            journal.mark_verifying(second.request_id, revision_id="rev-2", revision_number=1)
            first_id, second_id = first.request_id, second.request_id

        with _journal(tmp_path) as journal:
            seen_on_resume: list[RequestState] = []

            async def resume(request: JournalRequest) -> None:
                row = journal.get(first_id)
                assert row is not None
                seen_on_resume.append(row.state)
                await processor.complete(request)

            processor = FakeProcessor(journal, resume=resume)
            service = _service(journal, processor)
            await service.start()

            await _until(lambda: _states(journal) == {RequestState.COMPLETE: 2})
            await service.aclose()
            assert processor.calls == [("resume", 2), ("process", 1)]
            assert seen_on_resume == [RequestState.WAITING]
            row = journal.get(first_id)
            assert row is not None and row.attempts == 1  # the interrupted attempt counts
            resumed = journal.get(second_id)
            assert resumed is not None and resumed.revision_id == "rev-2"

    asyncio.run(scenario())


def test_shutdown_drains_in_flight_work_within_timeout(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _journal(tmp_path) as journal:
            started = asyncio.Event()

            async def slow(request: JournalRequest) -> None:
                started.set()
                await asyncio.sleep(0.1)
                await processor.complete(request)

            processor = FakeProcessor(journal, process=slow)
            service = _service(journal, processor, shutdown_timeout_seconds=2.0)
            await service.start()
            assert await service.schedule(_job(1))
            await asyncio.wait_for(started.wait(), 2)

            await service.aclose()
            assert _states(journal) == {RequestState.COMPLETE: 1}

    asyncio.run(scenario())


def test_shutdown_cancels_overdue_work_and_leaves_it_recoverable(tmp_path: Path) -> None:
    async def scenario() -> None:
        events: list[str] = []
        with _journal(tmp_path) as journal:
            started = asyncio.Event()

            async def hang(_request: JournalRequest) -> None:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    events.append("finalized")

            service = _service(
                journal, FakeProcessor(journal, process=hang), shutdown_timeout_seconds=0.05
            )
            await service.start()
            assert await service.schedule(_job(1))
            await asyncio.wait_for(started.wait(), 2)

            await service.aclose()
            assert events == ["finalized"]
            row = journal.list_recent()[0]
            assert (row.state, row.attempts, row.error_code) == (
                RequestState.COLLECTING,
                0,
                None,
            )

        with _journal(tmp_path) as journal:
            processor = FakeProcessor(journal)
            service = _service(journal, processor)
            await service.start()
            await _until(lambda: _states(journal) == {RequestState.COMPLETE: 1})
            await service.aclose()
            assert processor.calls == [("process", 1)]

    asyncio.run(scenario())


def test_coalesced_request_is_processed_after_promotion(tmp_path: Path) -> None:
    async def scenario() -> None:
        with _journal(tmp_path) as journal:
            release = asyncio.Event()
            started = asyncio.Event()

            async def gated(request: JournalRequest) -> None:
                started.set()
                await release.wait()
                await processor.complete(request)

            processor = FakeProcessor(journal, process=gated)
            service = _service(journal, processor, max_running=2)
            await service.start()
            assert await service.schedule(_job(1, "d-1"))
            await asyncio.wait_for(started.wait(), 2)
            assert await service.schedule(_job(1, "d-2"))
            assert _states(journal) == {RequestState.COLLECTING: 1, RequestState.COALESCED: 1}

            release.set()
            await _until(lambda: _states(journal) == {RequestState.COMPLETE: 2})
            await service.aclose()
            assert processor.calls == [("process", 1), ("process", 1)]

    asyncio.run(scenario())
