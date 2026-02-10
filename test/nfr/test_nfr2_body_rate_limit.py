"""NFR2: Enforce request body size limit and token-bucket rate limiting on ingest."""
from __future__ import annotations

from fastapi.testclient import TestClient

from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _settings_body_limit(storage_root: str, max_bytes: int) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
            "hardening": {
                "rate_limit": {"enabled": False},
                "body_size_limit": {"max_bytes": max_bytes},
                "webhook": {"allow_unsigned": True},
            },
        }
    )


def _settings_rate_limit(storage_root: str) -> Settings:
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


def test_nfr2_body_over_limit_returns_413(tmp_path) -> None:
    """NFR2: Request body over max_bytes must be rejected with 413."""
    app = create_app(_settings_body_limit(str(tmp_path), max_bytes=10))
    client = TestClient(app)
    resp = client.post(
        "/ingest",
        content=b'{"ticket":{"id":123}}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 413
    assert resp.json() == {"detail": "request_too_large"}


def test_nfr2_rate_limit_returns_429(tmp_path) -> None:
    """NFR2: Ingest over rate limit must be rejected with 429."""
    app = create_app(_settings_rate_limit(str(tmp_path)))
    client = TestClient(app)
    assert client.post("/ingest", json={}).status_code == 202
    assert client.post("/ingest", json={}).status_code == 202
    resp = client.post("/ingest", json={})
    assert resp.status_code == 429
    assert resp.json() == {"detail": "rate_limited"}
