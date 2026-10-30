"""Verify the journal-derived admission counters and operator retry admission."""

from __future__ import annotations

import asyncio
from pathlib import Path

from chronikwerk.operations.job import TicketJob
from chronikwerk.operations.journal import (
    JournalOptions,
    JournalRequest,
    RequestJournal,
    RequestSource,
)
from chronikwerk.operations.scheduling import JournaledSchedulingService


class _IdleProcessor:
    """A processor that never runs; the service is not started."""

    async def process(self, _request: JournalRequest) -> None:
        raise AssertionError("not started")

    async def resume(self, _request: JournalRequest) -> None:
        raise AssertionError("not started")

    async def publish_pending(self, _request: JournalRequest) -> None:
        return None

    async def report_failure(self, _request: JournalRequest) -> None:
        return None


def _service(journal: RequestJournal) -> JournaledSchedulingService:
    """Build an unstarted scheduler that admits at most two waiting requests."""
    return JournaledSchedulingService(
        journal=journal,
        processor=_IdleProcessor(),
        max_pending=2,
        max_running=1,
        shutdown_timeout_seconds=1.0,
    )


def test_admission_is_bounded_and_closing_is_reported(tmp_path: Path) -> None:
    async def exercise(service: JournaledSchedulingService) -> None:
        assert await service.schedule(TicketJob(ticket_id=1))
        assert await service.schedule(TicketJob(ticket_id=2))
        assert not await service.schedule(TicketJob(ticket_id=3))
        admission = service.admission
        assert (admission.pending, admission.running, admission.closing) == (2, 0, False)
        await service.aclose()
        assert service.admission.closing
        assert not await service.schedule(TicketJob(ticket_id=4))

    with RequestJournal(JournalOptions(path=tmp_path / "journal.db")) as journal:
        asyncio.run(exercise(_service(journal)))
        assert sorted(row.ticket_id for row in journal.list_recent()) == [1, 2]


def test_operator_retry_is_journaled_as_a_retry(tmp_path: Path) -> None:
    with RequestJournal(JournalOptions(path=tmp_path / "journal.db")) as journal:
        service = _service(journal)

        assert asyncio.run(service.schedule_retry(ticket_id=123, request_id="request-1"))

        [row] = journal.list_recent()
        assert (row.ticket_id, row.source, row.delivery_id) == (123, RequestSource.RETRY, None)
