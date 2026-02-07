from __future__ import annotations

import hashlib
import hmac

from fastapi.testclient import TestClient

from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _test_settings(storage_root: str) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {
                "base_url": "https://zammad.example.local",
                "api_token": "test-token",
                "webhook_hmac_secret": "test-secret",
            },
            "storage": {"root": storage_root},
            "hardening": {"body_size_limit": {"max_bytes": 10}},
        }
    )


def _signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    return f"sha1={digest}"


def test_body_size_limit_triggers_before_hmac_verification(tmp_path) -> None:
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    body = b"x" * 100

    # Signature is well-formed but wrong for the actual body.
    signature = _signature(b"wrong-body", "test-secret")
    response = client.post(
        "/ingest",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature": signature,
        },
    )

    assert response.status_code == 413
    assert response.json() == {"detail": "request_too_large"}

