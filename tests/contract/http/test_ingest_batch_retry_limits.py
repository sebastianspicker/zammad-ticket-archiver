"""Pin batch bounds, dry runs, capacity rejection, and retry authorization over HTTP."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from chronikwerk.web.app import create_app
from tests.support.http_security_test_helpers import post_signed_json
from tests.support.scheduling import SchedulingSpy
from tests.support.settings_factory import make_settings

_SECRET = "test-secret"
_CAPACITY_BODY = {
    "detail": "Service is at background job capacity; retry later.",
    "code": "job_capacity_exhausted",
}


def _client(
    tmp_path, *, accept: bool = True, retry_token: str | None = None
) -> tuple[TestClient, SchedulingSpy]:
    """Build a signed-ingest client with an observable scheduler."""
    spy = SchedulingSpy(accept=accept)
    overrides: dict[str, Any] = {}
    if retry_token is not None:
        overrides["retry_bearer_token"] = retry_token
    settings = make_settings(str(tmp_path), secret=_SECRET, overrides=overrides)
    return TestClient(create_app(settings, scheduler=spy)), spy


def _batch(client: TestClient, size: int, path: str = "/ingest/batch"):
    """Post a signed batch of the given size."""
    payload = [{"ticket_id": index + 1} for index in range(size)]
    return post_signed_json(client, path, payload, secret=_SECRET)


def test_batch_of_101_is_rejected_without_scheduling(tmp_path) -> None:
    client, spy = _client(tmp_path)

    response = _batch(client, 101)

    assert response.status_code == 422
    assert response.json() == {
        "detail": "batch too large (max 100 items)",
        "code": "batch_too_large",
    }
    assert spy.scheduled == []


@pytest.mark.parametrize("dry_run", [False, True])
def test_empty_batch_is_rejected_without_scheduling(tmp_path, dry_run: bool) -> None:
    client, spy = _client(tmp_path)

    response = _batch(client, 0, "/ingest/batch?dry_run=true" if dry_run else "/ingest/batch")

    assert response.status_code == 422
    assert response.json() == {"detail": "batch must not be empty", "code": "batch_empty"}
    assert spy.scheduled == []


def test_batch_of_exactly_100_is_accepted(tmp_path) -> None:
    client, spy = _client(tmp_path)

    response = _batch(client, 100)

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "count": 100}
    assert len(spy.scheduled) == 100


def test_batch_dry_run_counts_without_scheduling(tmp_path) -> None:
    client, spy = _client(tmp_path)

    response = _batch(client, 3, "/ingest/batch?dry_run=true")

    assert response.status_code == 202
    assert response.json() == {"status": "dry_run_accepted", "count": 3}
    assert spy.scheduled == []


def test_single_ingest_reports_capacity_exhaustion_with_retry_after(tmp_path) -> None:
    client, _spy = _client(tmp_path, accept=False)

    response = post_signed_json(client, "/ingest", {"ticket_id": 1}, secret=_SECRET)

    assert response.status_code == 503
    assert response.json() == _CAPACITY_BODY
    assert response.headers["Retry-After"] == "1"


def test_batch_ingest_reports_capacity_exhaustion_with_retry_after(tmp_path) -> None:
    client, _spy = _client(tmp_path, accept=False)

    response = _batch(client, 2)

    assert response.status_code == 503
    assert response.json() == _CAPACITY_BODY
    assert response.headers["Retry-After"] == "1"


def test_retry_reports_capacity_exhaustion_with_retry_after(tmp_path) -> None:
    client, _spy = _client(tmp_path, accept=False, retry_token="retry-token")

    response = client.post("/retry/9", headers={"Authorization": "Bearer retry-token"})

    assert response.status_code == 503
    assert response.json() == _CAPACITY_BODY
    assert response.headers["Retry-After"] == "1"


def test_retry_rejects_wrong_token(tmp_path) -> None:
    client, spy = _client(tmp_path, retry_token="retry-token")

    response = client.post("/retry/9", headers={"Authorization": "Bearer nope"})

    assert response.status_code == 401
    assert response.json() == {"detail": "unauthorized"}
    assert spy.retries == []


def test_retry_without_configured_token_is_service_unavailable(tmp_path) -> None:
    client, spy = _client(tmp_path)

    response = client.post("/retry/9", headers={"Authorization": "Bearer anything"})

    assert response.status_code == 503
    assert response.json() == {"detail": "retry_token_not_configured"}
    assert spy.retries == []


@pytest.mark.parametrize("ticket_id", ["0", "-3", str(2**63)])
def test_retry_rejects_non_positive_ticket_id(tmp_path, ticket_id) -> None:
    client, spy = _client(tmp_path, retry_token="retry-token")

    response = client.post(f"/retry/{ticket_id}", headers={"Authorization": "Bearer retry-token"})

    assert response.status_code == 422
    assert spy.retries == []


def test_shutdown_rejects_ingest_with_shutting_down_error(tmp_path) -> None:
    client, spy = _client(tmp_path)
    spy.accepting = False

    response = post_signed_json(client, "/ingest", {"ticket_id": 1}, secret=_SECRET)

    assert response.status_code == 503
    assert response.json() == {"detail": "Service is shutting down", "code": "shutting_down"}
    assert spy.scheduled == []
