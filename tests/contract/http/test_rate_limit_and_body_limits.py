"""Pin rate-limit and request-size enforcement order at the HTTP boundary."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from chronikwerk.web.app import create_app
from tests.support.asgi import asgi_post, body_chunks
from tests.support.hmac_test_helpers import sign_body
from tests.support.http_security_test_helpers import (
    assert_json_error,
    make_body_limit_settings,
    make_rate_limit_settings,
    post_ingest,
)
from tests.support.scheduling import SchedulingSpy
from tests.support.settings_factory import make_settings

_SECRET = "test-secret"
_BODY = b'{"ticket_id":7}'


def _rate_limited_client(tmp_path, *, header: str | None = None) -> TestClient:
    """Build a client allowing a burst of two requests and no refill."""
    settings = make_rate_limit_settings(str(tmp_path), secret=_SECRET)
    if header is not None:
        settings = make_settings(
            str(tmp_path),
            secret=_SECRET,
            overrides={
                "hardening": {
                    "rate_limit": {
                        "enabled": True,
                        "rps": 0,
                        "burst": 2,
                        "client_key_header": header,
                    }
                }
            },
        )
    return TestClient(create_app(settings, scheduler=SchedulingSpy()))


def _signed_post(client: TestClient, **extra: str):
    """Post a correctly signed ingest request with optional extra headers."""
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature": sign_body(_BODY, _SECRET),
        **extra,
    }
    return client.post("/ingest", content=_BODY, headers=headers)


def _forged_post(client: TestClient, **extra: str):
    """Post an ingest request with a wrong signature and optional extra headers."""
    headers = {
        "Content-Type": "application/json",
        "X-Hub-Signature": "sha256=" + "00" * 32,
        **extra,
    }
    return client.post("/ingest", content=_BODY, headers=headers)


def test_signed_webhook_bursts_are_never_rate_limited(tmp_path) -> None:
    client = _rate_limited_client(tmp_path)

    statuses = {_signed_post(client).status_code for _ in range(20)}

    assert statuses == {202}


def test_refused_requests_drain_the_bucket_and_then_get_429(tmp_path) -> None:
    client = _rate_limited_client(tmp_path)

    statuses = [_forged_post(client).status_code for _ in range(2)]
    third = _forged_post(client)

    assert statuses == [403, 403]
    assert_json_error(third, status_code=429, code="rate_limited")
    assert third.headers["Connection"] == "close"


def test_an_exhausted_bucket_also_holds_back_its_signed_requests(tmp_path) -> None:
    client = _rate_limited_client(tmp_path)
    for _ in range(2):
        _forged_post(client)

    assert _signed_post(client).status_code == 429


def test_client_key_header_buckets_clients_separately(tmp_path) -> None:
    client = _rate_limited_client(tmp_path, header="X-Forwarded-For")

    for _ in range(2):
        assert _forged_post(client, **{"X-Forwarded-For": "198.51.100.1"}).status_code == 403
    limited = _forged_post(client, **{"X-Forwarded-For": "198.51.100.1"})
    other = _signed_post(client, **{"X-Forwarded-For": "198.51.100.2"})

    assert limited.status_code == 429
    assert other.status_code == 202


def test_client_key_header_uses_first_forwarded_address(tmp_path) -> None:
    client = _rate_limited_client(tmp_path, header="X-Forwarded-For")

    for hop in ("10.0.0.1", "10.0.0.2"):
        _forged_post(client, **{"X-Forwarded-For": f"198.51.100.1, {hop}"})
    third = _forged_post(client, **{"X-Forwarded-For": "198.51.100.1, 10.0.0.3"})

    assert third.status_code == 429


def test_missing_client_key_header_falls_back_to_connection_address(tmp_path) -> None:
    client = _rate_limited_client(tmp_path, header="X-Forwarded-For")

    statuses = [_forged_post(client).status_code for _ in range(3)]

    assert statuses == [403, 403, 429]


def test_rate_limit_applies_before_signature_check(tmp_path) -> None:
    client = _rate_limited_client(tmp_path)
    for _ in range(2):
        post_ingest(client, _BODY, "sha256=" + "00" * 32)

    response = post_ingest(client, _BODY, "sha256=" + "00" * 32)

    assert_json_error(response, status_code=429, code="rate_limited")


def test_healthz_is_never_rate_limited(tmp_path) -> None:
    client = _rate_limited_client(tmp_path)

    statuses = {client.get("/healthz").status_code for _ in range(5)}

    assert statuses == {200}


def test_oversized_content_length_is_rejected_with_413(tmp_path) -> None:
    settings = make_body_limit_settings(str(tmp_path), 10, secret=_SECRET)
    client = TestClient(create_app(settings, scheduler=SchedulingSpy()))

    response = _signed_post(client)

    assert_json_error(response, status_code=413, code="request_too_large")
    assert response.headers["Connection"] == "close"


def test_oversized_unsigned_request_is_413_not_403(tmp_path) -> None:
    settings = make_body_limit_settings(str(tmp_path), 10, secret=_SECRET)
    client = TestClient(create_app(settings, scheduler=SchedulingSpy()))

    response = client.post("/ingest", content=_BODY)

    assert response.status_code == 413


def test_root_path_request_still_has_the_ingest_body_limit(tmp_path) -> None:
    settings = make_body_limit_settings(str(tmp_path), 10, secret=_SECRET)
    client = TestClient(
        create_app(settings, scheduler=SchedulingSpy()),
        root_path="/archive",
    )

    response = client.post("/archive/ingest", content=_BODY)

    assert_json_error(response, status_code=413, code="request_too_large")


def test_body_at_the_limit_is_accepted(tmp_path) -> None:
    settings = make_body_limit_settings(str(tmp_path), len(_BODY), secret=_SECRET)
    client = TestClient(create_app(settings, scheduler=SchedulingSpy()))

    assert _signed_post(client).status_code == 202


def test_oversized_chunked_body_is_rejected_with_413(tmp_path) -> None:
    settings = make_body_limit_settings(str(tmp_path), 10, secret=_SECRET)
    app = create_app(settings, scheduler=SchedulingSpy())
    headers = {"x-hub-signature": sign_body(_BODY, _SECRET)}

    result = asyncio.run(
        asgi_post(app, "/ingest", body_chunks(_BODY[:6], _BODY[6:]), headers=headers)
    )

    assert result.status == 413
    assert result.json() == {"detail": "request_too_large", "code": "request_too_large"}


def test_root_path_request_still_has_the_ingest_rate_limit(tmp_path) -> None:
    settings = make_rate_limit_settings(str(tmp_path), secret=_SECRET)
    client = TestClient(
        create_app(settings, scheduler=SchedulingSpy()),
        root_path="/archive",
    )
    headers = {"Content-Type": "application/json", "X-Hub-Signature": "sha256=" + "00" * 32}

    statuses = [
        client.post("/archive/ingest", content=_BODY, headers=headers).status_code for _ in range(3)
    ]

    assert statuses == [403, 403, 429]
