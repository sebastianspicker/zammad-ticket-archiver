"""NFR1: Verify webhook payload with HMAC-SHA1; fail closed when secret configured."""
from __future__ import annotations

import hashlib
import hmac

from fastapi.testclient import TestClient

from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _settings(storage_root: str, *, secret: str | None = "test-secret") -> Settings:
    zammad: dict[str, object] = {
        "base_url": "https://zammad.example.local",
        "api_token": "test-token",
    }
    if secret is not None:
        zammad["webhook_hmac_secret"] = secret
    return Settings.from_mapping({"zammad": zammad, "storage": {"root": storage_root}})


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    return f"sha1={digest}"


def test_nfr1_invalid_signature_returns_403(tmp_path) -> None:
    """NFR1: Invalid or wrong HMAC must be rejected with 403."""
    app = create_app(_settings(str(tmp_path)))
    client = TestClient(app)
    body = b'{"ticket_id":123}'
    response = client.post(
        "/ingest",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature": _sign(body, "wrong")},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "forbidden", "code": "forbidden"}


def test_nfr1_no_secret_returns_503_unless_allow_unsigned(tmp_path) -> None:
    """NFR1: Fail closed when no webhook secret and allow_unsigned is false."""
    app = create_app(_settings(str(tmp_path), secret=None))
    client = TestClient(app)
    response = client.post("/ingest", json={"ticket_id": 123})
    assert response.status_code == 503
    data = response.json()
    assert data == {"detail": "webhook_auth_not_configured", "code": "webhook_auth_not_configured"}


def test_nfr1_valid_signature_returns_202(tmp_path, monkeypatch) -> None:
    """NFR1: Valid HMAC must allow request through (202)."""
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    async def noop(*_args: object, **_kwargs: object) -> None:
        pass

    monkeypatch.setattr(ingest_route, "process_ticket", noop)
    app = create_app(_settings(str(tmp_path)))
    client = TestClient(app)
    body = b'{"ticket":{"id":456}}'
    response = client.post(
        "/ingest",
        content=body,
        headers={"Content-Type": "application/json", "X-Hub-Signature": _sign(body, "test-secret")},
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": True, "ticket_id": 456}
