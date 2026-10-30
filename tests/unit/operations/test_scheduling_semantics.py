"""Pin journal-before-202 admission and shutdown rejection through the HTTP application."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chronikwerk.operations.history import EmptyHistory
from chronikwerk.operations.journal import (
    JournalOptions,
    JournalRequest,
    RequestJournal,
    RequestState,
)
from chronikwerk.operations.scheduling import JournaledSchedulingService
from chronikwerk.web.app import create_app
from tests.support.hmac_test_helpers import sign_body
from tests.support.settings_factory import make_settings

_SECRET = "test-secret"


class _IdleProcessor:
    """A processor that must never run: these tests stop before workers lease anything."""

    async def process(self, _request: JournalRequest) -> None:
        raise AssertionError("no work may start in this test")

    async def resume(self, _request: JournalRequest) -> None:
        raise AssertionError("no work may resume in this test")

    async def publish_pending(self, _request: JournalRequest) -> None:
        return None

    async def report_failure(self, _request: JournalRequest) -> None:
        return None


def _service(journal: RequestJournal, *, max_pending: int = 10) -> JournaledSchedulingService:
    """Build a scheduler over ``journal`` whose processor must never run."""
    return JournaledSchedulingService(
        journal=journal,
        processor=_IdleProcessor(),
        max_pending=max_pending,
        max_running=1,
        shutdown_timeout_seconds=1.0,
    )


def _post(client: TestClient, body: bytes, delivery_id: str | None = None) -> int:
    """Post a signed webhook body and return the status code."""
    headers = {"Content-Type": "application/json", "X-Hub-Signature": sign_body(body, _SECRET)}
    if delivery_id is not None:
        headers["X-Zammad-Delivery"] = delivery_id
    return client.post("/ingest", content=body, headers=headers).status_code


def test_ingest_journals_the_request_before_answering_202(tmp_path: Path) -> None:
    settings = make_settings(str(tmp_path), secret=_SECRET)
    with RequestJournal(JournalOptions(path=tmp_path / "journal.db")) as journal:
        # Without the lifespan no worker runs, so the 202 can only come from the journal.
        client = TestClient(create_app(settings, scheduler=_service(journal)))

        assert _post(client, b'{"ticket_id":7}', "delivery-1") == 202
        assert _post(client, b'{"ticket_id":7}', "delivery-1") == 202

        [row] = journal.list_recent()
        assert (row.ticket_id, row.delivery_id) == (7, "delivery-1")
        assert row.state is RequestState.WAITING


def test_full_journal_answers_capacity_exhausted(tmp_path: Path) -> None:
    settings = make_settings(str(tmp_path), secret=_SECRET)
    with RequestJournal(JournalOptions(path=tmp_path / "journal.db")) as journal:
        client = TestClient(create_app(settings, scheduler=_service(journal, max_pending=1)))

        assert _post(client, b'{"ticket_id":1}') == 202
        response = client.post(
            "/ingest",
            content=b'{"ticket_id":2}',
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature": sign_body(b'{"ticket_id":2}', _SECRET),
            },
        )

        assert response.status_code == 503
        assert response.json()["code"] == "job_capacity_exhausted"
        assert [row.ticket_id for row in journal.list_recent()] == [1]


def test_ingest_returns_shutting_down_after_lifespan_exit(tmp_path: Path) -> None:
    settings = make_settings(str(tmp_path), secret=_SECRET)
    with RequestJournal(JournalOptions(path=tmp_path / "journal.db")) as journal:
        client = TestClient(create_app(settings, scheduler=_service(journal)))

        with client:
            pass
        response = client.post(
            "/ingest",
            content=b'{"ticket_id":1}',
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature": sign_body(b'{"ticket_id":1}', _SECRET),
            },
        )

        assert response.status_code == 503
        assert response.json() == {"detail": "Service is shutting down", "code": "shutting_down"}
        assert journal.list_recent() == []


def test_app_reads_the_history_its_scheduler_derives_from_the_journal(tmp_path: Path) -> None:
    """Operator views show the scheduler's history, and a mismatched history is rejected."""
    settings = make_settings(str(tmp_path))
    with RequestJournal(JournalOptions(path=tmp_path / "journal.db")) as journal:
        scheduler = _service(journal)
        app = create_app(settings, scheduler=scheduler)

        assert app.state.history is scheduler.history
        assert asyncio.run(scheduler.schedule_retry(ticket_id=9, request_id="r-9"))
        [entry] = app.state.history.read(10)
        assert (entry["ticket_id"], entry["status"]) == (9, "accepted")
        with pytest.raises(ValueError, match="scheduler's history"):
            create_app(settings, scheduler=scheduler, history=EmptyHistory())
