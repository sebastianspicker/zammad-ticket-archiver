from __future__ import annotations

from fastapi.testclient import TestClient

from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _test_settings(storage_root: str) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
            "hardening": {
                "rate_limit": {"enabled": False, "rps": 1, "burst": 1},
                "body_size_limit": {"max_bytes": 10},
                "webhook": {"allow_unsigned": True, "allow_unsigned_when_no_secret": True},
            },
        }
    )


def test_body_size_limit_triggers_on_ingest(tmp_path) -> None:
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    resp = client.post(
        "/ingest",
        content=b'{"ticket":{"id":123}}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    assert resp.json() == {"detail": "request_too_large", "code": "request_too_large"}
    assert resp.headers.get("X-Request-Id")
