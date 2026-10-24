"""Pin webhook signature verification in legacy and strict delivery-bound modes."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from chronikwerk.web.app import create_app
from tests.support.asgi import asgi_post, body_chunks
from tests.support.hmac_test_helpers import sign_body, sign_strict, sign_zammad_native
from tests.support.http_security_test_helpers import assert_json_error, post_ingest
from tests.support.scheduling import SchedulingSpy
from tests.support.settings_factory import make_settings

_SECRET = "test-secret"
_BODY = b'{"ticket_id":7}'


def _client(tmp_path, *, strict: bool = False) -> tuple[TestClient, SchedulingSpy]:
    """Build a signed-ingest client with an observable scheduler."""
    spy = SchedulingSpy()
    settings = make_settings(str(tmp_path), secret=_SECRET, require_delivery_id=strict)
    return TestClient(create_app(settings, scheduler=spy)), spy


def test_legacy_mode_accepts_body_signature(tmp_path) -> None:
    client, spy = _client(tmp_path)

    response = post_ingest(client, _BODY, sign_body(_BODY, _SECRET))

    assert response.status_code == 202
    assert len(spy.scheduled) == 1


def test_legacy_mode_rejects_wrong_signature(tmp_path) -> None:
    client, spy = _client(tmp_path)

    response = post_ingest(client, _BODY, sign_body(_BODY, "other-secret"))

    assert_json_error(response, status_code=403, code="forbidden")
    # NOTE: a post-body signature mismatch does not send "Connection: close",
    # unlike the pre-body rejections (missing signature, missing delivery id).
    assert "connection" not in response.headers
    assert spy.scheduled == []


def test_missing_signature_header_closes_the_connection(tmp_path) -> None:
    client, _spy = _client(tmp_path)

    response = client.post("/ingest", content=_BODY)

    assert_json_error(response, status_code=403, code="forbidden")
    assert response.headers["Connection"] == "close"


def test_legacy_mode_rejects_signature_over_different_body(tmp_path) -> None:
    client, _spy = _client(tmp_path)

    response = post_ingest(client, _BODY, sign_body(b'{"ticket_id":8}', _SECRET))

    assert response.status_code == 403


def test_uppercase_algorithm_prefix_is_accepted(tmp_path) -> None:
    client, _spy = _client(tmp_path)
    signature = sign_body(_BODY, _SECRET).replace("sha256=", "SHA256=")

    response = post_ingest(client, _BODY, signature)

    assert response.status_code == 202


def test_legacy_mode_accepts_zammad_native_sha1_signature(tmp_path) -> None:
    client, spy = _client(tmp_path)

    response = post_ingest(client, _BODY, sign_zammad_native(_BODY, _SECRET))

    assert response.status_code == 202
    assert len(spy.scheduled) == 1


def test_legacy_mode_rejects_wrong_sha1_signature(tmp_path) -> None:
    client, spy = _client(tmp_path)

    response = post_ingest(client, _BODY, "sha1=" + "00" * 20)

    assert response.status_code == 403
    assert spy.scheduled == []


def test_truncated_sha1_digest_is_rejected(tmp_path) -> None:
    client, _spy = _client(tmp_path)

    response = post_ingest(client, _BODY, sign_zammad_native(_BODY, _SECRET)[:-2])

    assert response.status_code == 403


def test_strict_mode_accepts_delivery_bound_signature(tmp_path) -> None:
    client, spy = _client(tmp_path, strict=True)

    response = post_ingest(
        client, _BODY, sign_strict("delivery-1", _BODY, _SECRET), delivery_id="delivery-1"
    )

    assert response.status_code == 202
    assert spy.scheduled[0].delivery_id == "delivery-1"


def test_strict_mode_rejects_body_only_signature(tmp_path) -> None:
    client, spy = _client(tmp_path, strict=True)

    response = post_ingest(client, _BODY, sign_body(_BODY, _SECRET), delivery_id="delivery-1")

    assert response.status_code == 403
    assert spy.scheduled == []


def test_strict_mode_rejects_sha1_even_when_delivery_bound(tmp_path) -> None:
    client, spy = _client(tmp_path, strict=True)

    response = post_ingest(
        client,
        _BODY,
        sign_strict("delivery-1", _BODY, _SECRET, algorithm="sha1"),
        delivery_id="delivery-1",
    )

    assert response.status_code == 403
    assert spy.scheduled == []


def test_strict_mode_rejects_signature_bound_to_other_delivery(tmp_path) -> None:
    client, _spy = _client(tmp_path, strict=True)

    response = post_ingest(
        client, _BODY, sign_strict("delivery-1", _BODY, _SECRET), delivery_id="delivery-2"
    )

    assert response.status_code == 403


def test_strict_mode_requires_delivery_header(tmp_path) -> None:
    client, spy = _client(tmp_path, strict=True)

    response = post_ingest(client, _BODY, sign_body(_BODY, _SECRET))

    assert_json_error(response, status_code=400, code="missing_delivery_id")
    assert spy.scheduled == []


def test_strict_mode_rejects_blank_delivery_header(tmp_path) -> None:
    client, _spy = _client(tmp_path, strict=True)

    response = post_ingest(client, _BODY, sign_body(_BODY, _SECRET), delivery_id="   ")

    assert response.status_code == 400
    assert response.json()["code"] == "missing_delivery_id"


def test_retry_does_not_require_webhook_signature(tmp_path) -> None:
    spy = SchedulingSpy()
    settings = make_settings(
        str(tmp_path), secret=_SECRET, overrides={"retry_bearer_token": "retry-token"}
    )
    client = TestClient(create_app(settings, scheduler=spy))

    response = client.post("/retry/5", headers={"Authorization": "Bearer retry-token"})

    assert response.status_code == 202
    assert [ticket for ticket, _ in spy.retries] == [5]


def _signed_headers(body: bytes) -> dict[str, str]:
    """Build legacy-mode headers for a direct ASGI request."""
    return {
        "content-type": "application/json",
        "content-length": str(len(body)),
        "x-hub-signature": sign_body(body, _SECRET),
    }


def test_chunked_body_reaches_route_intact(tmp_path) -> None:
    spy = SchedulingSpy()
    app = create_app(make_settings(str(tmp_path), secret=_SECRET), scheduler=spy)
    chunks = (b'{"ticket_', b'id":', b"7}")

    result = asyncio.run(
        asgi_post(app, "/ingest", body_chunks(*chunks), headers=_signed_headers(_BODY))
    )

    assert result.status == 202
    assert result.json() == {"status": "accepted", "ticket_id": 7}
    assert spy.scheduled[0].payload["ticket_id"] == 7


def test_disconnect_before_body_completes_fails_closed(tmp_path) -> None:
    spy = SchedulingSpy()
    app = create_app(make_settings(str(tmp_path), secret=_SECRET), scheduler=spy)
    partial = [{"type": "http.request", "body": _BODY[:5], "more_body": True}]

    result = asyncio.run(asgi_post(app, "/ingest", partial, headers=_signed_headers(_BODY)))

    assert result.status == 403
    assert spy.scheduled == []


@pytest.mark.parametrize("path", ["/ingest", "/ingest/batch"])
def test_blank_secret_is_service_misconfigured(tmp_path, path) -> None:
    client = TestClient(create_app(make_settings(str(tmp_path), secret=" ")))

    response = client.post(path, content=b"[]")

    assert_json_error(response, status_code=503, code="webhook_auth_not_configured")
