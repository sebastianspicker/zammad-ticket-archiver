"""Schedule bounded ticket work through one application-owned service."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import structlog

from chronikwerk.operations.admission import AdmissionClosed, JobAdmission
from chronikwerk.operations.history import record_history_event
from chronikwerk.operations.job import FORCE_REPROCESS_KEY, REQUEST_ID_KEY, extract_ticket_id
from chronikwerk.operations.shutdown import is_shutting_down, track_task

TicketProcessor = Callable[[str | None, dict[str, Any]], Awaitable[Any]]

log = structlog.get_logger(__name__)


class TicketScheduler(Protocol):
    """Scheduling capability consumed by HTTP and administration delivery."""

    def schedule(self, *, delivery_id: str | None, payload: dict[str, Any]) -> bool:
        """Schedule one admitted delivery when capacity is available."""
        ...

    def schedule_batch(self, jobs: list[tuple[str | None, dict[str, Any]]]) -> bool:
        """Schedule an all-or-nothing group of admitted deliveries."""
        ...

    def schedule_retry(self, *, ticket_id: int, request_id: str | None) -> bool:
        """Schedule one forced operator retry when capacity is available."""
        ...


class TicketSchedulingService:
    """Own in-process task admission and retry scheduling for one application."""

    def __init__(
        self,
        *,
        admission: JobAdmission,
        process_ticket: TicketProcessor,
    ) -> None:
        self._admission = admission
        self._process_ticket = process_ticket

    def schedule(self, *, delivery_id: str | None, payload: dict[str, Any]) -> bool:
        """Reserve and create one ticket job."""
        return self.schedule_batch([(delivery_id, payload)])

    def schedule_batch(self, jobs: list[tuple[str | None, dict[str, Any]]]) -> bool:
        """Reserve and create a complete job group without partial admission."""
        if is_shutting_down():
            return False
        if jobs and not self._admission.try_reserve(len(jobs)):
            return False

        created = 0
        try:
            for delivery_id, payload in jobs:
                self._create_task(delivery_id=delivery_id, payload=payload)
                created += 1
        except Exception:
            self._admission.cancel_reservation(len(jobs) - created)
            raise
        return True

    def schedule_retry(self, *, ticket_id: int, request_id: str | None) -> bool:
        """Schedule a forced retry without delivery-ID deduplication."""
        payload: dict[str, Any] = {
            "ticket_id": ticket_id,
            REQUEST_ID_KEY: request_id,
            FORCE_REPROCESS_KEY: True,
        }
        return self.schedule(delivery_id=None, payload=payload)

    def _create_task(self, *, delivery_id: str | None, payload: dict[str, Any]) -> None:
        task = asyncio.create_task(self._run(delivery_id=delivery_id, payload=payload))
        track_task(task)
        record_history_event(
            "accepted",
            extract_ticket_id(payload),
            delivery_id=delivery_id,
            request_id=(
                str(payload.get(REQUEST_ID_KEY))
                if payload.get(REQUEST_ID_KEY) is not None
                else None
            ),
        )

    async def _run(self, *, delivery_id: str | None, payload: dict[str, Any]) -> None:
        ticket_id = extract_ticket_id(payload)
        if ticket_id is None:
            log.warning("ingest.skip_background_no_ticket_id", delivery_id=delivery_id)
            self._admission.cancel_reservation()
            return

        bound: dict[str, object] = {"ticket_id": ticket_id}
        if delivery_id:
            bound["delivery_id"] = delivery_id
        try:
            await self._admission.acquire()
        except AdmissionClosed:
            log.info("ingest.job_cancelled_during_shutdown", ticket_id=ticket_id)
            return

        structlog.contextvars.bind_contextvars(**bound)
        try:
            await self._process_ticket(delivery_id, payload)
        except Exception:  # pylint: disable=broad-exception-caught
            log.exception(
                "ingest.process_ticket_unhandled_error",
                ticket_id=ticket_id,
                delivery_id=delivery_id,
            )
        finally:
            structlog.contextvars.unbind_contextvars(*bound.keys())
            await self._admission.release()
