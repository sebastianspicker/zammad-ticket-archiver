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
                "rate_limit": {"enabled": True, "rps": 0, "burst": 2},
                "body_size_limit": {"max_bytes": 1024 * 1024},
                "webhook": {"allow_unsigned": True},
            },
        }
    )


def test_rate_limit_triggers_on_ingest(tmp_path) -> None:
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    assert client.post("/ingest", json={}).status_code == 202
    assert client.post("/ingest", json={}).status_code == 202

    resp = client.post("/ingest", json={})
    assert resp.status_code == 429
    assert resp.json() == {"detail": "rate_limited"}
    assert resp.headers.get("X-Request-Id")
