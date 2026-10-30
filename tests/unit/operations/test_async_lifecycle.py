"""Check cancellation and shutdown through public operational boundaries."""

from __future__ import annotations

import asyncio
import threading

import pytest

from chronikwerk.concurrency import run_sync_cancellation_safe
from chronikwerk.operations.job import TicketJob
from chronikwerk.operations.journal import (
    JournalOptions,
    JournalRequest,
    RequestJournal,
    RequestState,
)
from chronikwerk.operations.scheduling import JournaledSchedulingService
from chronikwerk.web.app import create_app
from tests.support.settings_factory import make_settings


@pytest.mark.parametrize("fails", [False, True])
def test_repeated_cancellation_waits_for_blocking_work(fails: bool) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def work() -> None:
        started.set()
        assert release.wait(5)
        finished.set()
        if fails:
            raise ValueError("synthetic worker failure")

    async def scenario() -> None:
        task = asyncio.create_task(run_sync_cancellation_safe(work))
        try:
            assert await asyncio.to_thread(started.wait, 5)
            for _ in range(2):
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                assert not finished.is_set()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError) as error:
            await task
        assert finished.is_set()
        if fails:
            assert "ValueError" in " ".join(error.value.__notes__)

    asyncio.run(scenario())


def test_lifespan_closes_resources_after_cancelled_job_finalizers(tmp_path) -> None:
    events: list[str] = []
    settings = make_settings(
        str(tmp_path), overrides={"admission": {"shutdown_timeout_seconds": 0.01}}
    )
    started = asyncio.Event()

    class Processor:
        async def process(self, _request: JournalRequest) -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                events.append("job finalized")

        async def resume(self, _request: JournalRequest) -> None:
            raise AssertionError("nothing to resume")

        async def publish_pending(self, _request: JournalRequest) -> None:
            return None

        async def report_failure(self, _request: JournalRequest) -> None:
            return None

    async def cleanup() -> None:
        events.append("client closed")

    async def scenario(journal: RequestJournal) -> None:
        scheduler = JournaledSchedulingService(
            journal=journal,
            processor=Processor(),
            max_pending=10,
            max_running=1,
            shutdown_timeout_seconds=settings.admission.shutdown_timeout_seconds,
        )
        app = create_app(settings, scheduler=scheduler, cleanup=cleanup)
        async with app.router.lifespan_context(app):
            assert await scheduler.schedule(TicketJob(ticket_id=1))
            await asyncio.wait_for(started.wait(), 1)
        assert events == ["job finalized", "client closed"]

    with RequestJournal(JournalOptions(path=tmp_path / "journal.db")) as journal:
        asyncio.run(scenario(journal))
        # A cancelled request keeps its state so the next start recovers it.
        [row] = journal.list_recent()
        assert row.state is RequestState.COLLECTING
