from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _test_settings(storage_root: str, *, secret: str | None) -> Settings:
    zammad: dict[str, object] = {
        "base_url": "https://zammad.example.local",
        "api_token": "test-token",
    }
    if secret is not None:
        zammad["webhook_hmac_secret"] = secret
    return Settings.from_mapping({"zammad": zammad, "storage": {"root": storage_root}})


def _test_settings_unsigned_ok(storage_root: str) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
            "hardening": {"webhook": {"allow_unsigned": True, "allow_unsigned_when_no_secret": True}},
        }
    )


def _test_settings_legacy_secret(storage_root: str, *, secret: str) -> Settings:
    return Settings.from_mapping(
        {
            "server": {"webhook_shared_secret": secret},
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
        }
    )


def _sign(body: bytes, secret: str, *, algorithm: str = "sha1") -> str:
    if algorithm == "sha256":
        digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return f"sha256={digest}"
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    return f"sha1={digest}"


def test_valid_signature_passes(tmp_path, monkeypatch) -> None:
    secret = "test-secret"
    app = create_app(_test_settings(str(tmp_path), secret=secret))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    async def _stub_process_ticket(delivery_id, payload, settings) -> None:
        return None

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    body = b'{"ticket":{"id":123}}'
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign(body, secret),
        },
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": True, "ticket_id": 123}


def test_valid_sha256_signature_passes(tmp_path, monkeypatch) -> None:
    secret = "test-secret"
    app = create_app(_test_settings(str(tmp_path), secret=secret))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    async def _stub_process_ticket(delivery_id, payload, settings) -> None:
        return None

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    body = b'{"ticket":{"id":456}}'
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign(body, secret, algorithm="sha256"),
        },
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": True, "ticket_id": 456}


def test_invalid_signature_is_rejected(tmp_path) -> None:
    secret = "test-secret"
    app = create_app(_test_settings(str(tmp_path), secret=secret))
    client = TestClient(app)

    body = b'{"ticket_id":123}'
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign(body, "wrong-secret"),
        },
    )
    assert response.status_code == 403
    assert response.headers.get("X-Request-Id")


def test_missing_signature_is_rejected_when_secret_configured(tmp_path) -> None:
    secret = "test-secret"
    app = create_app(_test_settings(str(tmp_path), secret=secret))
    client = TestClient(app)

    response = client.post("/ingest", json={"ticket": {"id": 123}})
    assert response.status_code == 403


def test_missing_signature_is_allowed_when_secret_unset(tmp_path) -> None:
    app = create_app(_test_settings(str(tmp_path), secret=None))
    client = TestClient(app)

    response = client.post("/ingest", json={})
    assert response.status_code == 503


def test_missing_signature_is_allowed_only_when_allow_unsigned_enabled(tmp_path) -> None:
    app = create_app(_test_settings_unsigned_ok(str(tmp_path)))
    client = TestClient(app)

    response = client.post("/ingest", json={"ticket": {"id": 1}})
    assert response.status_code == 202


@pytest.mark.parametrize(
    "signature",
    [
        "sha1",  # missing "="
        f"sha256={'00' * 20}",  # wrong algorithm
        "sha1=not-hex",
        "sha1=00",  # wrong length
    ],
)
def test_malformed_signature_is_rejected(tmp_path, signature: str) -> None:
    secret = "test-secret"
    app = create_app(_test_settings(str(tmp_path), secret=secret))
    client = TestClient(app)

    body = b'{"ticket":{"id":123}}'
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": signature,
        },
    )
    assert response.status_code == 403


def test_signature_must_match_request_body_bytes(tmp_path, monkeypatch) -> None:
    secret = "test-secret"
    app = create_app(_test_settings(str(tmp_path), secret=secret))

    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    async def _stub_process_ticket(_delivery_id, _payload, _settings) -> None:
        raise AssertionError("process_ticket must not run when signature verification fails")

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    body = b'{"ticket":{"id":123}}'
    wrong_body = body + b" "
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign(wrong_body, secret),
        },
    )
    assert response.status_code == 403


def test_default_app_fails_closed_without_settings() -> None:
    from zammad_pdf_archiver.app.server import app as default_app

    client = TestClient(default_app)
    response = client.post("/ingest", json={"ticket": {"id": 123}})

    assert response.status_code == 503
    data = response.json()
    assert data == {"detail": "webhook_auth_not_configured", "code": "webhook_auth_not_configured"}
    assert response.headers.get("X-Request-Id")


def test_valid_signature_passes_with_legacy_shared_secret(tmp_path, monkeypatch) -> None:
    secret = "legacy-secret"
    app = create_app(_test_settings_legacy_secret(str(tmp_path), secret=secret))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    async def _stub_process_ticket(delivery_id, payload, settings) -> None:
        return None

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    body = b'{"ticket":{"id":123}}'
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": _sign(body, secret),
        },
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": True, "ticket_id": 123}
