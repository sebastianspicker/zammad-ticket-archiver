"""Verify the public bounded-admission lifecycle."""

from __future__ import annotations

import asyncio
from typing import Any

from chronikwerk.operations.admission import AdmissionClosed, JobAdmission
from chronikwerk.operations.job import FORCE_REPROCESS_KEY, REQUEST_ID_KEY
from chronikwerk.operations.scheduling import TicketSchedulingService
from chronikwerk.operations.shutdown import clear_shutting_down, wait_for_tasks


def test_admission_is_bounded_and_closes_queued_work() -> None:
    async def exercise() -> None:
        admission = JobAdmission(max_pending=1, max_running=1)
        assert admission.try_reserve()
        assert admission.try_reserve()
        assert not admission.try_reserve()
        await admission.acquire()
        assert (admission.pending, admission.running) == (1, 1)
        await admission.close()
        await admission.release()
        try:
            await admission.acquire()
        except AdmissionClosed:
            return
        raise AssertionError("closed admission accepted queued work")

    asyncio.run(exercise())


def test_operator_retry_preserves_archive_job_metadata() -> None:
    async def exercise() -> None:
        captured: list[tuple[str | None, dict[str, Any]]] = []

        async def process(delivery_id: str | None, payload: dict[str, Any]) -> None:
            captured.append((delivery_id, payload))

        clear_shutting_down()
        scheduler = TicketSchedulingService(
            admission=JobAdmission(max_pending=1, max_running=1),
            process_ticket=process,
        )

        assert scheduler.schedule_retry(ticket_id=123, request_id="request-1")
        await wait_for_tasks()

        assert captured == [
            (
                None,
                {
                    "ticket_id": 123,
                    REQUEST_ID_KEY: "request-1",
                    FORCE_REPROCESS_KEY: True,
                },
            )
        ]

    asyncio.run(exercise())
