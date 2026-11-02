"""Schedule bounded ticket work through one application-owned service.

``JournaledSchedulingService`` admits requests durably into the request journal and runs them
on a bounded worker pool.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

import structlog

from chronikwerk.concurrency import run_sync_cancellation_safe
from chronikwerk.operations.history import HistoryReader, JournalHistoryView
from chronikwerk.operations.job import TicketJob
from chronikwerk.operations.journal import (
    AdmitItem,
    AdmitResult,
    JournalRequest,
    RequestJournal,
    RequestSource,
    RequestState,
)
from chronikwerk.operations.metrics import (
    admission_pending,
    admission_rejected_total,
    admission_running,
    oldest_waiting_seconds,
    requests_by_state,
)
from chronikwerk.operations.processing import RequestProcessor
from chronikwerk.redaction import scrub_secrets_in_text

log = structlog.get_logger(__name__)

_IN_FLIGHT_STATES = (RequestState.COLLECTING, RequestState.VERIFYING)
DEFAULT_MAINTENANCE_INTERVAL_SECONDS = 900.0
DEFAULT_JOURNAL_RETENTION_SECONDS = 90 * 24 * 3600.0


class AdmissionView(Protocol):
    """Admission counters that operator views read."""

    @property
    def pending(self) -> int:
        """Return the number of admitted requests waiting for a worker."""
        ...

    @property
    def running(self) -> int:
        """Return the number of requests currently being processed."""
        ...

    @property
    def max_pending(self) -> int:
        """Return the configured bound on waiting requests."""
        ...

    @property
    def max_running(self) -> int:
        """Return the configured number of workers."""
        ...

    @property
    def closing(self) -> bool:
        """Return whether new work is being rejected."""
        ...


class TicketScheduler(Protocol):
    """Scheduling capability consumed by HTTP and administration delivery."""

    async def schedule(self, job: TicketJob) -> bool:
        """Schedule one admitted delivery when capacity is available."""
        ...

    async def schedule_batch(self, jobs: list[TicketJob]) -> bool:
        """Schedule an all-or-nothing group of admitted deliveries."""
        ...

    async def schedule_retry(self, *, ticket_id: int, request_id: str | None) -> bool:
        """Schedule one forced operator retry when capacity is available."""
        ...

    @property
    def accepting(self) -> bool:
        """Return whether new work may still be scheduled."""
        ...

    @property
    def admission(self) -> AdmissionView:
        """Return the bounded admission whose counters operators observe."""
        ...

    @property
    def history(self) -> HistoryReader:
        """Return the job history operator views read."""
        ...

    async def aclose(self) -> None:
        """Stop accepting work and drain or cancel tracked jobs."""
        ...


def _retry_job(ticket_id: int, request_id: str | None) -> TicketJob:
    return TicketJob(ticket_id=ticket_id, request_id=request_id, force_reprocess=True)


@dataclass(frozen=True, slots=True)
class JournalAdmission:
    """Read-only snapshot of journal-derived admission counters."""

    pending: int
    running: int
    max_pending: int
    max_running: int
    closing: bool


def _source(job: TicketJob) -> RequestSource:
    return RequestSource.RETRY if job.force_reprocess else RequestSource.WEBHOOK


def _log_context(request: JournalRequest) -> dict[str, object]:
    bound: dict[str, object] = {"ticket_id": request.ticket_id, "request_id": request.request_id}
    if request.delivery_id:
        bound["delivery_id"] = request.delivery_id
    return bound


class JournaledSchedulingService:
    """Admit requests into the durable journal and process them with a bounded worker pool.

    All methods run on the application's event loop; blocking journal calls run off-loop.
    ``start`` returns at once: a background task recovers interrupted rows, resumes VERIFYING
    rows, then starts ``max_running`` workers. A maintenance task runs at start and every
    ``maintenance_interval_seconds``: it retries open Zammad updates, reports failures recorded
    outside the processor, recovers rows left in flight without a worker (a failed journal
    write) like a restart does, purges terminal rows older than ``journal_retention_seconds`` and
    runs ``housekeeping``; each step is isolated and rows being processed are skipped.
    ``aclose`` stops admission and leasing, drains in-flight work for up to
    ``shutdown_timeout_seconds`` and then cancels it; cancelled rows keep their journal state
    and are recovered by the next ``start``. The journal itself is owned by the caller.
    """

    def __init__(
        self,
        *,
        journal: RequestJournal,
        processor: RequestProcessor,
        max_pending: int,
        max_running: int,
        shutdown_timeout_seconds: float,
        poll_interval_seconds: float = 5.0,
        maintenance_interval_seconds: float = DEFAULT_MAINTENANCE_INTERVAL_SECONDS,
        journal_retention_seconds: float = DEFAULT_JOURNAL_RETENTION_SECONDS,
        housekeeping: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._journal = journal
        self._processor = processor
        self._max_pending = max_pending
        self._max_running = max_running
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._maintenance_interval_seconds = maintenance_interval_seconds
        self._journal_retention_seconds = journal_retention_seconds
        self._housekeeping = housekeeping
        self._history = JournalHistoryView(journal)
        self._accepting = True
        self._stopping = False
        self._started = False
        self._wakeup = asyncio.Event()
        self._workers: list[asyncio.Task[None]] = []
        self._background: list[asyncio.Task[None]] = []
        self._in_flight: set[str] = set()
        self._maintenance_lock = asyncio.Lock()
        self._pending = 0
        self._running = 0

    @property
    def accepting(self) -> bool:
        """Return whether new work may still be scheduled."""
        return self._accepting

    @property
    def admission(self) -> JournalAdmission:
        """Return the journal counters observed at the last admission or state change."""
        return JournalAdmission(
            pending=self._pending,
            running=self._running,
            max_pending=self._max_pending,
            max_running=self._max_running,
            closing=not self._accepting,
        )

    @property
    def history(self) -> JournalHistoryView:
        """Return the journal-derived history operator views read."""
        return self._history

    def export_journal_metrics(self) -> None:
        """Set the per-state request gauge and the oldest-waiting age from the journal."""
        for state, count in self._journal.count_by_state().items():
            requests_by_state.labels(state=state.value).set(count)
        oldest_waiting_seconds.set(self._journal.oldest_waiting_age_seconds() or 0.0)

    async def schedule(self, job: TicketJob) -> bool:
        """Journal one request; duplicates and coalesced requests count as accepted."""
        if not self._accepting:
            return self._reject(1)
        result = await run_sync_cancellation_safe(
            self._journal.admit,
            ticket_id=job.ticket_id,
            source=_source(job),
            delivery_id=job.delivery_id,
            max_pending=self._max_pending,
            trigger_login=job.trigger_login,
        )
        return await self._admitted([job], [result])

    async def schedule_batch(self, jobs: list[TicketJob]) -> bool:
        """Journal a whole batch in one transaction, or nothing when it does not fit."""
        if not self._accepting:
            return self._reject(len(jobs))
        if not jobs:
            return True
        items = [
            AdmitItem(job.ticket_id, _source(job), job.delivery_id, trigger_login=job.trigger_login)
            for job in jobs
        ]
        results = await run_sync_cancellation_safe(
            self._journal.admit_many, items, max_pending=self._max_pending
        )
        return await self._admitted(jobs, results)

    async def schedule_retry(self, *, ticket_id: int, request_id: str | None) -> bool:
        """Journal a forced operator retry (source ``retry``)."""
        return await self.schedule(_retry_job(ticket_id, request_id))

    @staticmethod
    def _reject(count: int) -> bool:
        admission_rejected_total.inc(max(1, count))
        return False

    async def _admitted(
        self, jobs: Sequence[TicketJob], results: Sequence[AdmitResult] | None
    ) -> bool:
        if results is None or any(result.outcome == "full" for result in results):
            return self._reject(len(jobs))
        for job, result in zip(jobs, results, strict=True):
            log.info(
                "scheduling.request_journaled",
                ticket_id=job.ticket_id,
                delivery_id=job.delivery_id,
                http_request_id=job.request_id,
                outcome=result.outcome,
                journal_request_id=result.request_id or result.active_request_id,
            )
        self._wakeup.set()
        await self._refresh_counts()
        return True

    async def start(self) -> None:
        """Schedule recovery, resumption and the worker pool, plus the maintenance task."""
        if self._started:
            raise RuntimeError("JournaledSchedulingService.start() was already called")
        self._started = True
        self._background = [
            asyncio.create_task(self._start_workers(), name="journal-startup"),
            asyncio.create_task(self._maintain(), name="journal-maintenance"),
        ]

    async def _start_workers(self) -> None:
        """Recover the journal, resume VERIFYING rows, then start the worker pool."""
        try:
            _requeued, verifying = await run_sync_cancellation_safe(
                self._journal.recover_after_restart
            )
        except Exception:  # pylint: disable=broad-exception-caught
            log.exception("scheduling.recovery_failed")
            verifying = []
        for request in verifying:
            if self._stopping:
                break
            await self._handle(request, self._processor.resume)
        await self._refresh_counts()
        if not self._stopping:
            self._workers = [
                asyncio.create_task(self._work(), name=f"journal-worker-{index}")
                for index in range(self._max_running)
            ]

    async def aclose(self) -> None:
        """Stop accepting and leasing, drain in-flight work, then cancel what remains."""
        self._accepting = False
        self._stopping = True
        self._wakeup.set()
        startup, maintenance = self._background or (None, None)
        if maintenance is not None:
            maintenance.cancel()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._shutdown_timeout_seconds
        if startup is not None and not startup.done():
            _done, _overdue = await asyncio.wait([startup], timeout=self._shutdown_timeout_seconds)
            startup.cancel()
        workers = [task for task in self._workers if not task.done()]
        if workers:
            remaining = max(0.0, deadline - loop.time())
            _done, overdue = await asyncio.wait(workers, timeout=remaining)
            for task in overdue:
                task.cancel()
        await asyncio.gather(*self._background, *workers, return_exceptions=True)

    async def _maintain(self) -> None:
        """Run the maintenance steps at start and then every maintenance interval."""
        while not self._stopping:
            await self.run_maintenance()
            await asyncio.sleep(self._maintenance_interval_seconds)

    async def run_maintenance(self) -> None:
        """Run every maintenance step once; a failing step is logged and the rest continue."""
        steps: list[tuple[str, Callable[[], Awaitable[None]]]] = [
            ("publish_pending", self._publish_pending),
            ("report_failures", self._report_failures),
            ("recover_orphans", self._recover_orphans),
            ("purge_terminal", self._purge_terminal),
        ]
        if self._housekeeping is not None:
            steps.append(("housekeeping", self._housekeeping))
        async with self._maintenance_lock:
            for name, step in steps:
                if self._stopping:
                    return
                try:
                    await step()
                except Exception:  # pylint: disable=broad-exception-caught
                    log.exception("scheduling.maintenance_failed", step=name)

    async def _publish_pending(self) -> None:
        rows = await run_sync_cancellation_safe(self._journal.pending_zammad_updates)
        await self._each_idle(rows, self._processor.publish_pending, "publish_pending")

    async def _report_failures(self) -> None:
        rows = await run_sync_cancellation_safe(self._journal.pending_failure_reports)
        await self._each_idle(rows, self._processor.report_failure, "report_failure")

    async def _recover_orphans(self) -> None:
        """Recover rows left in flight by a failed journal write, as a restart would.

        Every row a worker or the startup resume handles is in ``_in_flight`` (one process per
        journal), so an in-flight row older than one maintenance interval that is not there
        has no owner left.
        """
        if not self._workers:
            return  # startup recovery and resumption still own the in-flight rows
        rows = await run_sync_cancellation_safe(
            self._journal.stuck, older_than_seconds=self._maintenance_interval_seconds
        )
        await self._each_idle(rows, self._recover_orphan, "recover_orphan")

    async def _recover_orphan(self, request: JournalRequest) -> None:
        """Resume an orphaned VERIFYING row; send an orphaned COLLECTING row back to WAITING."""
        log.warning("scheduling.orphan_recovered", state=request.state.value)
        if request.state is RequestState.VERIFYING:
            # The processor decides from the store whether the commit can be finished.
            await self._handle(request, self._processor.resume)
            return
        await run_sync_cancellation_safe(
            self._journal.fail_transient,
            request.request_id,
            error_code="internal_error",
            error_detail="Request was left in flight without a worker",
        )
        self._wakeup.set()

    async def _purge_terminal(self) -> None:
        purged = await run_sync_cancellation_safe(
            self._journal.purge_terminal, older_than_seconds=self._journal_retention_seconds
        )
        if purged:
            log.info("scheduling.journal_purged", rows=purged)

    async def _each_idle(
        self,
        rows: Sequence[JournalRequest],
        step: Callable[[JournalRequest], Awaitable[None]],
        name: str,
    ) -> None:
        """Run ``step`` for every unchanged row no worker is handling; failures stay per row."""
        for request in rows:
            if self._stopping:
                return
            with structlog.contextvars.bound_contextvars(**_log_context(request)):
                try:
                    if await self._still_idle(request):
                        await step(request)
                except Exception:  # pylint: disable=broad-exception-caught
                    log.exception("scheduling.maintenance_row_failed", step=name)

    async def _still_idle(self, request: JournalRequest) -> bool:
        """Skip rows a worker handles or that changed since the maintenance snapshot."""
        if request.request_id in self._in_flight:
            return False
        current = await run_sync_cancellation_safe(self._journal.get, request.request_id)
        return (
            current is not None
            and current.updated_at == request.updated_at
            and request.request_id not in self._in_flight
        )

    async def _work(self) -> None:
        while not self._stopping:
            self._wakeup.clear()
            request = await self._lease()
            if request is None:
                await self._idle()
                continue
            # Let another idle worker look for more due work.
            self._wakeup.set()
            await self._handle(request, self._processor.process)

    async def _lease(self) -> JournalRequest | None:
        try:
            return await run_sync_cancellation_safe(self._journal.lease_next)
        except Exception:  # pylint: disable=broad-exception-caught
            log.exception("scheduling.lease_failed")
            return None

    async def _idle(self) -> None:
        """Wait for an admission, or the poll interval so due back-off retries wake up."""
        with suppress(TimeoutError):
            await asyncio.wait_for(self._wakeup.wait(), timeout=self._poll_interval_seconds)

    async def _handle(
        self,
        request: JournalRequest,
        step: Callable[[JournalRequest], Awaitable[None]],
    ) -> None:
        # Claim the row before the first await, so maintenance never sees it without an owner.
        self._in_flight.add(request.request_id)
        try:
            await self._refresh_counts()
            with structlog.contextvars.bound_contextvars(**_log_context(request)):
                detail: str | None = None
                try:
                    await step(request)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    log.exception("scheduling.processor_failed")
                    detail = f"{type(exc).__name__}: {exc}"
                await self._settle(request, detail)
        finally:
            self._in_flight.discard(request.request_id)
        await self._refresh_counts()

    async def _settle(self, request: JournalRequest, detail: str | None) -> None:
        """Record ``internal_error`` when the processor left its row in flight."""
        try:
            current = await run_sync_cancellation_safe(self._journal.get, request.request_id)
            if current is None or current.state not in _IN_FLIGHT_STATES:
                return
            if detail is None:
                detail = f"Processor returned while the request was {current.state.value}"
                log.error("scheduling.processor_left_request_in_flight", state=current.state)
            await run_sync_cancellation_safe(
                self._journal.fail_transient,
                request.request_id,
                error_code="internal_error",
                error_detail=scrub_secrets_in_text(detail),
            )
        except Exception:  # pylint: disable=broad-exception-caught
            log.exception("scheduling.settle_failed")

    async def _refresh_counts(self) -> None:
        try:
            counts = await run_sync_cancellation_safe(self._journal.count_by_state)
        except Exception:  # pylint: disable=broad-exception-caught
            log.exception("scheduling.count_failed")
            return
        self._pending = counts[RequestState.WAITING]
        self._running = sum(counts[state] for state in _IN_FLIGHT_STATES)
        admission_pending.set(self._pending)
        admission_running.set(self._running)
