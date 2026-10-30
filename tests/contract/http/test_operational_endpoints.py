"""Pin the /metrics, /jobs/history, and /healthz operational endpoints."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from chronikwerk.operations.history import HistoryReader, JournalHistoryView
from chronikwerk.operations.journal import JournalOptions, RequestJournal, RequestSource
from chronikwerk.operations.scheduling import JournaledSchedulingService
from chronikwerk.web.app import create_app
from tests.support.settings_factory import make_settings

_METRIC_NAMES = (
    "processed_total",
    "skipped_total",
    "failed_total",
    "render_seconds",
    "sign_seconds",
    "total_seconds",
    "admission_pending",
    "admission_running",
    "admission_rejected_total",
)
_AUTH = {"Authorization": "Bearer ops-token"}


def _client(tmp_path, history: HistoryReader | None = None, **observability: Any) -> TestClient:
    """Create a client with the given observability overrides."""
    settings = make_settings(str(tmp_path), overrides={"observability": observability})
    return TestClient(create_app(settings, history=history))


def test_metrics_route_is_absent_when_disabled(tmp_path) -> None:
    client = _client(tmp_path)

    assert client.get("/metrics", headers=_AUTH).status_code == 404


def test_metrics_without_configured_token_is_service_unavailable(tmp_path) -> None:
    client = _client(tmp_path, metrics_enabled=True)

    response = client.get("/metrics")

    assert response.status_code == 503
    assert response.json() == {"detail": "metrics_auth_not_configured"}


def test_metrics_rejects_wrong_or_missing_token(tmp_path) -> None:
    client = _client(tmp_path, metrics_enabled=True, metrics_bearer_token="ops-token")

    wrong = client.get("/metrics", headers={"Authorization": "Bearer nope"})
    missing = client.get("/metrics")

    assert wrong.status_code == 401
    assert missing.status_code == 401
    assert wrong.json() == {"detail": "unauthorized"}


def test_metrics_exposes_every_service_metric(tmp_path) -> None:
    client = _client(tmp_path, metrics_enabled=True, metrics_bearer_token="ops-token")

    response = client.get("/metrics", headers=_AUTH)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    for name in _METRIC_NAMES:
        assert name in response.text, name


def test_history_route_is_absent_when_disabled(tmp_path) -> None:
    client = _client(tmp_path)

    assert client.get("/jobs/history", headers=_AUTH).status_code == 404


def test_history_rejects_wrong_token(tmp_path) -> None:
    client = _client(tmp_path, history_enabled=True, history_bearer_token="ops-token")

    response = client.get("/jobs/history", headers={"Authorization": "Bearer nope"})

    assert response.status_code == 401


def test_history_without_configured_token_is_service_unavailable(tmp_path) -> None:
    client = _client(tmp_path, history_enabled=True)

    response = client.get("/jobs/history")

    assert response.status_code == 503
    assert response.json() == {"detail": "history_token_not_configured"}


def _journal(tmp_path) -> RequestJournal:
    """Open a request journal next to the archive root."""
    return RequestJournal(JournalOptions(path=tmp_path / "journal.sqlite3"))


def _admit(journal: RequestJournal, ticket_id: int, delivery_id: str | None = None) -> str:
    """Journal one webhook request and return its request ID."""
    result = journal.admit(
        ticket_id=ticket_id, source=RequestSource.WEBHOOK, delivery_id=delivery_id
    )
    assert result.request_id is not None
    return result.request_id


def _lease(journal: RequestJournal) -> str:
    """Lease the oldest waiting request and return its request ID."""
    leased = journal.lease_next()
    assert leased is not None
    return leased.request_id


def test_history_lists_entries_newest_first_and_filters_by_ticket(tmp_path) -> None:
    with _journal(tmp_path) as journal:
        client = _client(
            tmp_path,
            history=JournalHistoryView(journal),
            history_enabled=True,
            history_bearer_token="ops-token",
        )
        _admit(journal, 22)
        journal.complete(_lease(journal), revision_id="rev-22")
        _admit(journal, 11, "d-1")

        everything = client.get("/jobs/history", headers=_AUTH)
        only_first = client.get("/jobs/history?ticket_id=11", headers=_AUTH)

    assert everything.status_code == 200
    entries = everything.json()["entries"]
    assert [(e["ticket_id"], e["status"]) for e in entries] == [(11, "accepted"), (22, "processed")]
    assert entries[0]["delivery_id"] == "d-1"
    assert [e["ticket_id"] for e in only_first.json()["entries"]] == [11]


def test_history_entries_keep_their_wire_shape_and_id_sequence(tmp_path) -> None:
    """History entries expose the documented keys and count string ids from one."""
    with _journal(tmp_path) as journal:
        client = _client(
            tmp_path,
            history=JournalHistoryView(journal),
            history_enabled=True,
            history_bearer_token="ops-token",
        )
        _admit(journal, 12)
        journal.fail_permanent(_lease(journal), error_code="boom_code", error_detail="boom")
        accepted_id = _admit(journal, 11, "d-1")

        entries = client.get("/jobs/history", headers=_AUTH).json()["entries"]

    assert [entry["id"] for entry in entries] == ["2", "1"]
    for entry in entries:
        assert set(entry) == {
            "id",
            "status",
            "ticket_id",
            "classification",
            "message",
            "delivery_id",
            "request_id",
            "created_at",
            "updated_at",
        }
        assert isinstance(entry["created_at"], float)
    assert {
        key: value for key, value in entries[0].items() if key not in {"created_at", "updated_at"}
    } == {
        "id": "2",
        "status": "accepted",
        "ticket_id": 11,
        "classification": None,
        "message": "",
        "delivery_id": "d-1",
        "request_id": accepted_id,
    }
    assert entries[1]["status"] == "failed"
    assert entries[1]["classification"] == "Permanent"
    assert entries[1]["message"] == "boom_code: boom"


class _IdleProcessor:
    """A processor for a scheduler whose workers never start."""

    async def process(self, _request: object) -> None:
        raise AssertionError("not started")

    async def resume(self, _request: object) -> None:
        raise AssertionError("not started")

    async def publish_pending(self, _request: object) -> None:
        return None

    async def report_failure(self, _request: object) -> None:
        return None


def test_metrics_report_journal_states_on_scrape(tmp_path) -> None:
    """Brief 5.12: per-state request counts and the oldest waiting age come from the journal."""
    settings = make_settings(
        str(tmp_path),
        overrides={"observability": {"metrics_enabled": True, "metrics_bearer_token": "ops-token"}},
    )
    with _journal(tmp_path) as journal:
        scheduler = JournaledSchedulingService(
            journal=journal,
            processor=_IdleProcessor(),
            max_pending=10,
            max_running=1,
            shutdown_timeout_seconds=1.0,
        )
        _admit(journal, 1)
        _admit(journal, 2)
        journal.fail_permanent(_lease(journal), error_code="boom_code", error_detail="boom")
        client = TestClient(create_app(settings, scheduler=scheduler))

        text = client.get("/metrics", headers=_AUTH).text

    assert 'chronikwerk_requests{state="waiting"} 1.0' in text
    assert 'chronikwerk_requests{state="failed"} 1.0' in text
    assert 'chronikwerk_requests{state="complete"} 0.0' in text
    assert "chronikwerk_oldest_waiting_seconds " in text


def test_healthz_reports_status_service_and_version(tmp_path) -> None:
    client = _client(tmp_path)

    body = client.get("/healthz").json()

    assert body["status"] == "ok"
    assert body["service"] == "chronikwerk"
    assert body["version"]
    assert "time" in body
    assert "checks" not in body


def test_healthz_omits_service_and_version_when_configured(tmp_path) -> None:
    client = _client(tmp_path, healthz_omit_version=True)

    body = client.get("/healthz").json()

    assert body["status"] == "ok"
    assert "service" not in body
    assert "version" not in body


def test_deep_healthz_reports_writable_storage(tmp_path) -> None:
    client = _client(tmp_path)

    body = client.get("/healthz?deep=true").json()

    assert body["status"] == "ok"
    assert body["checks"] == {"storage": {"writable": True}}


def test_deep_healthz_degrades_when_storage_root_is_missing(tmp_path) -> None:
    settings = make_settings(str(tmp_path / "missing"))
    client = TestClient(create_app(settings))

    body = client.get("/healthz?deep=true").json()

    assert body["status"] == "degraded"
    assert body["checks"] == {"storage": {"writable": False, "reason": "storage_unavailable"}}
