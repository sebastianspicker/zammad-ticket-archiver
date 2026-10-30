"""Exercise the public HTTP ingestion contract and its scheduling handoff."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from chronikwerk.configuration.models import Settings
from chronikwerk.web.app import create_app
from chronikwerk.web.constants import normalized_delivery_id
from tests.support.http_security_test_helpers import post_signed_json
from tests.support.scheduling import SchedulingSpy
from tests.support.settings_factory import make_settings

_WEBHOOK_SECRET = "test-webhook-secret"
_LEGACY_FORCE_REPROCESS_FIELD = "_force_reprocess"


def _settings(storage_root: str, *, overrides: dict[str, Any] | None = None) -> Settings:
    """Build validated HTTP-contract settings for one isolated archive root."""
    return make_settings(storage_root, secret=_WEBHOOK_SECRET, overrides=overrides)


def _client(tmp_path, *, accept: bool = True, overrides: dict[str, Any] | None = None):
    """Create a test client with an observable scheduler boundary."""
    scheduler = SchedulingSpy(accept=accept)
    app = create_app(_settings(str(tmp_path), overrides=overrides), scheduler=scheduler)
    return TestClient(app), scheduler


def _post(client: TestClient, path: str, payload: Any, **headers: str):
    """Post one correctly signed JSON request to the test application."""
    return post_signed_json(
        client,
        path,
        payload,
        secret=_WEBHOOK_SECRET,
        extra_headers=headers or None,
    )


def test_ingest_validates_payload_and_hands_off_sanitized_job(tmp_path) -> None:
    client, scheduler = _client(tmp_path)

    response = _post(
        client,
        "/ingest",
        {"ticket": {"id": 123}, _LEGACY_FORCE_REPROCESS_FIELD: True},
        **{"X-Zammad-Delivery": "delivery-xyz"},
    )

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "ticket_id": 123}
    assert response.headers["X-Request-Id"]
    assert len(scheduler.scheduled) == 1
    job = scheduler.scheduled[0]
    assert (job.ticket_id, job.delivery_id) == (123, "delivery-xyz")
    assert job.request_id
    # The body is never journaled, so a legacy marker cannot force reprocessing.
    assert job.force_reprocess is False


def test_ingest_rejects_invalid_payload_without_scheduling(tmp_path) -> None:
    client, scheduler = _client(tmp_path)

    response = _post(client, "/ingest", {"ticket_id": True})

    assert response.status_code == 422
    assert scheduler.scheduled == []


def test_batch_assigns_delivery_suffixes_and_per_item_tickets(tmp_path) -> None:
    client, scheduler = _client(tmp_path)

    response = _post(
        client,
        "/ingest/batch",
        [{"ticket": {"id": 111}}, {"ticket_id": 222}],
        **{"X-Zammad-Delivery": "delivery-batch"},
    )

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "count": 2}
    assert [(job.delivery_id, job.ticket_id) for job in scheduler.scheduled] == [
        ("delivery-batch:0", 111),
        ("delivery-batch:1", 222),
    ]


def test_ingest_dry_runs_and_capacity_failures_do_not_schedule(tmp_path) -> None:
    client, scheduler = _client(tmp_path, accept=False)

    dry_run = _post(client, "/ingest?dry_run=true", {"ticket_id": 123})
    overloaded = _post(client, "/ingest", {"ticket_id": 123})

    assert dry_run.json() == {"status": "dry_run_accepted", "ticket_id": 123}
    assert overloaded.status_code == 503
    assert overloaded.json()["code"] == "job_capacity_exhausted"
    assert len(scheduler.scheduled) == 1
    assert scheduler.scheduled[0].delivery_id is None
    assert scheduler.scheduled[0].ticket_id == 123
    assert scheduler.scheduled[0].request_id


def test_retry_requires_token_and_hands_off_authorized_request(tmp_path) -> None:
    client, scheduler = _client(tmp_path, overrides={"retry_bearer_token": "retry-token"})

    unauthorized = client.post("/retry/987")
    authorized = client.post("/retry/987", headers={"Authorization": "Bearer retry-token"})

    assert unauthorized.status_code == 401
    assert authorized.status_code == 202
    assert authorized.json() == {"status": "accepted", "ticket_id": 987}
    assert [ticket_id for ticket_id, _request_id in scheduler.retries] == [987]
    assert scheduler.retries[0][1]


def test_request_identifier_is_preserved_only_when_valid(tmp_path) -> None:
    client, _scheduler = _client(tmp_path)

    accepted = _post(client, "/ingest", {"ticket_id": 1}, **{"X-Request-Id": "request-1"})
    replaced = _post(client, "/ingest", {"ticket_id": 2}, **{"X-Request-Id": "not valid"})

    assert accepted.headers["X-Request-Id"] == "request-1"
    assert replaced.headers["X-Request-Id"] != "not valid"


def test_ingest_records_the_triggering_login(tmp_path) -> None:
    client, scheduler = _client(tmp_path)

    for payload in (
        {"ticket": {"id": 1, "updated_by": {"login": "fallback"}}, "user": {"login": "agent"}},
        {"ticket": {"id": 2, "updated_by": {"login": " editor "}}},
        {"ticket": {"id": 3, "updated_by": {"login": "-"}}, "user": {"login": "-"}},
        {"ticket": {"id": 4}, "user": {"login": 5}},
    ):
        assert _post(client, "/ingest", payload).status_code == 202
    batch = [{"ticket_id": 5, "user": {"login": "bulk"}}]
    assert _post(client, "/ingest/batch", batch).status_code == 202

    assert [job.trigger_login for job in scheduler.scheduled] == [
        "agent",
        "editor",
        None,
        None,
        "bulk",
    ]


def test_overlong_delivery_id_is_treated_as_absent(tmp_path) -> None:
    client, scheduler = _client(tmp_path)

    for delivery_id in ("d" * 128, "d" * 129):
        response = _post(client, "/ingest", {"ticket_id": 1}, **{"X-Zammad-Delivery": delivery_id})
        assert response.status_code == 202

    assert [job.delivery_id for job in scheduler.scheduled] == ["d" * 128, None]


def test_delivery_id_must_be_printable_ascii() -> None:
    assert normalized_delivery_id("  abc-123:x  ") == "abc-123:x"
    assert normalized_delivery_id("   ") is None
    assert normalized_delivery_id(None) is None
    assert normalized_delivery_id("tab\there") is None
    assert normalized_delivery_id("zustellung-ü") is None
