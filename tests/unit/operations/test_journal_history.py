"""Verify batch admission, journal paging and the journal-derived operator history."""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from chronikwerk.operations.history import JournalHistoryView
from chronikwerk.operations.journal import (
    AdmitItem,
    JournalOptions,
    JournalRequest,
    RequestJournal,
    RequestSource,
    RequestState,
)
from chronikwerk.operations.scheduling import JournaledSchedulingService
from chronikwerk.web.app import create_app
from tests.support.http_security_test_helpers import post_signed_json
from tests.support.settings_factory import make_settings

_SECRET = "test-webhook-secret"


def _journal(tmp_path: Path) -> RequestJournal:
    """Open a real journal inside ``tmp_path``."""
    return RequestJournal(JournalOptions(path=tmp_path / "journal.db"))


def _item(ticket_id: int, delivery_id: str | None = None) -> AdmitItem:
    """Build one webhook admission item."""
    return AdmitItem(ticket_id, RequestSource.WEBHOOK, delivery_id)


def test_admit_many_applies_per_item_rules_in_one_transaction(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        journal.admit(ticket_id=9, source=RequestSource.WEBHOOK, delivery_id="seen")

        results = journal.admit_many(
            [_item(1), _item(1), _item(2, "seen"), _item(3)], max_pending=3
        )

        assert results is not None
        assert [result.outcome for result in results] == [
            "admitted",
            "coalesced",
            "duplicate_delivery",
            "admitted",
        ]
        assert results[1].active_request_id == results[0].request_id
        assert journal.count_by_state()[RequestState.WAITING] == 3


def test_admit_many_stores_nothing_when_the_batch_does_not_fit(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        journal.admit(ticket_id=9, source=RequestSource.WEBHOOK)

        assert journal.admit_many([_item(1, "a"), _item(2, "b")], max_pending=2) is None
        assert [row.ticket_id for row in journal.list_recent()] == [9]
        assert journal.admit_many([], max_pending=0) == []


def _history_fixture(journal: RequestJournal) -> None:
    """Journal one request per state: waiting, running, complete, failed and coalesced."""
    for ticket_id in (1, 2, 3, 4):
        journal.admit(
            ticket_id=ticket_id, source=RequestSource.WEBHOOK, delivery_id=f"d-{ticket_id}"
        )
    leased = {}
    for _ in range(3):
        row = journal.lease_next()
        assert row is not None
        leased[row.ticket_id] = row.request_id
    journal.complete(leased[1], revision_id="rev-1")
    journal.fail_permanent(leased[2], error_code="record_class_missing", error_detail="no class")
    journal.admit(ticket_id=3, source=RequestSource.WEBHOOK)


def test_history_view_maps_journal_states_to_operator_statuses(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        _history_fixture(journal)
        history = JournalHistoryView(journal)

        items = history.read(10)

        assert [(item["ticket_id"], item["status"]) for item in items] == [
            (3, "coalesced"),
            (4, "accepted"),
            (3, "running"),
            (2, "failed"),
            (1, "processed"),
        ]
        failed = items[3]
        assert failed["classification"] == "Permanent"
        assert failed["message"] == "record_class_missing: no class"
        assert failed["delivery_id"] == "d-2"
        assert isinstance(failed["created_at"], float)
        assert [item["ticket_id"] for item in history.read(10, statuses={"failed"})] == [2]
        assert history.read(10, statuses={"skipped"}) == []
        assert [item["status"] for item in history.read(10, 3)] == ["coalesced", "running"]


def test_history_view_shows_not_triggered_failures_as_skipped(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        _history_fixture(journal)
        journal.admit(ticket_id=5, source=RequestSource.WEBHOOK)
        leased = journal.lease_next()
        assert leased is not None and leased.ticket_id == 4
        journal.fail_permanent(leased.request_id, error_code="not_triggered", error_detail="no tag")
        history = JournalHistoryView(journal)

        def tickets(statuses: set[str]) -> list[tuple[int, str]]:
            return [
                (item["ticket_id"], item["status"]) for item in history.read(10, statuses=statuses)
            ]

        assert tickets({"skipped"}) == [(4, "skipped")]
        assert tickets({"failed"}) == [(2, "failed")]
        assert tickets({"failed", "skipped"}) == [(4, "skipped"), (2, "failed")]
        assert tickets({"skipped", "accepted"}) == [(5, "accepted"), (4, "skipped")]
        [skipped] = history.read(10, statuses={"skipped"})
        assert skipped["classification"] is None


def test_history_view_pages_with_stable_rowid_cursors(tmp_path: Path) -> None:
    with _journal(tmp_path) as journal:
        _history_fixture(journal)
        history = JournalHistoryView(journal)

        first = history.read(2)
        second = history.read(2, before_id=int(first[-1]["id"]))
        rest = history.read(10, before_id=int(second[-1]["id"]))

        ids = [int(item["id"]) for item in first + second + rest]
        assert ids == sorted(ids, reverse=True)
        assert len(set(ids)) == 5
        assert history.read(0) == []


class _NeverRuns:
    """Processor for HTTP tests whose application lifespan never starts workers."""

    async def process(self, _request: object) -> None:
        raise AssertionError("workers are not started in this test")

    async def resume(self, _request: object) -> None:
        raise AssertionError("workers are not started in this test")

    async def publish_pending(self, _request: object) -> None:
        return None

    async def report_failure(self, _request: object) -> None:
        return None


def test_ingest_routes_answer_as_before_with_the_journaled_scheduler(tmp_path: Path) -> None:
    settings = make_settings(str(tmp_path), secret=_SECRET)
    with _journal(tmp_path) as journal:
        scheduler = JournaledSchedulingService(
            journal=journal,
            processor=_NeverRuns(),
            max_pending=1,
            max_running=1,
            shutdown_timeout_seconds=1.0,
        )
        app = create_app(settings, scheduler=scheduler)
        client = TestClient(app)

        def post(path: str, payload: object, delivery_id: str | None = None):
            return post_signed_json(client, path, payload, secret=_SECRET, delivery_id=delivery_id)

        accepted = post("/ingest", {"ticket_id": 1}, "d-1")
        duplicate = post("/ingest", {"ticket_id": 1}, "d-1")
        full = post("/ingest", {"ticket_id": 2}, "d-2")
        batch_full = post("/ingest/batch", [{"ticket_id": 3}, {"ticket_id": 4}], "b-1")

        assert (accepted.status_code, accepted.json()) == (
            202,
            {"status": "accepted", "ticket_id": 1},
        )
        assert duplicate.status_code == 202
        assert (full.status_code, full.json()["code"]) == (503, "job_capacity_exhausted")
        assert full.headers["Retry-After"] == "1"
        assert batch_full.status_code == 503
        assert [row.ticket_id for row in journal.list_recent()] == [1]
        assert app.state.history is scheduler.history


class _Completes:
    """Processor that completes every leased request."""

    def __init__(self, journal: RequestJournal) -> None:
        self.journal = journal

    async def process(self, request: JournalRequest) -> None:
        """Complete the request with a revision named after its ticket."""
        self.journal.complete(request.request_id, revision_id=f"rev-{request.ticket_id}")

    async def resume(self, request: JournalRequest) -> None:
        """Fail loudly: no VERIFYING rows exist in these tests."""
        raise AssertionError(f"unexpected resume of {request.request_id}")

    async def publish_pending(self, _request: JournalRequest) -> None:
        return None

    async def report_failure(self, _request: JournalRequest) -> None:
        return None


def test_application_lifespan_starts_and_closes_the_journaled_scheduler(tmp_path: Path) -> None:
    settings = make_settings(str(tmp_path), secret=_SECRET)
    with _journal(tmp_path) as journal:
        scheduler = JournaledSchedulingService(
            journal=journal,
            processor=_Completes(journal),
            max_pending=5,
            max_running=1,
            shutdown_timeout_seconds=1.0,
            poll_interval_seconds=0.02,
        )
        with TestClient(create_app(settings, scheduler=scheduler)) as client:
            response = post_signed_json(client, "/ingest", {"ticket_id": 7}, secret=_SECRET)
            assert response.status_code == 202
            deadline = time.monotonic() + 3
            while journal.count_by_state()[RequestState.COMPLETE] != 1:
                assert time.monotonic() < deadline, "request was not processed"
                time.sleep(0.01)

        assert scheduler.accepting is False
