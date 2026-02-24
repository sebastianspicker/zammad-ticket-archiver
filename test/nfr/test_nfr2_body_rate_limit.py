"""NFR2: Enforce request body size limit and token-bucket rate limiting on ingest."""
from __future__ import annotations

from fastapi.testclient import TestClient

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _settings_body_limit(storage_root: str, max_bytes: int) -> Settings:
    return make_settings(
        storage_root,
        overrides={
            "hardening": {
                "rate_limit": {"enabled": False},
                "body_size_limit": {"max_bytes": max_bytes},
            }
        },
    )


def _settings_rate_limit(storage_root: str) -> Settings:
    return make_settings(
        storage_root,
        overrides={
            "hardening": {
                "rate_limit": {"enabled": True, "rps": 0, "burst": 2},
                "body_size_limit": {"max_bytes": 1024 * 1024},
            }
        },
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
    assert resp.json() == {"detail": "request_too_large", "code": "request_too_large"}


def test_nfr2_rate_limit_returns_429(tmp_path, monkeypatch) -> None:
    """NFR2: Ingest over rate limit must be rejected with 429."""
    async def _stub_process_ticket(delivery_id, payload, settings) -> None:  # noqa: ANN001, ARG001
        return None

    app = create_app(_settings_rate_limit(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)
    payload = {"ticket": {"id": 1}}
    assert client.post("/ingest", json=payload).status_code == 202
    assert client.post("/ingest", json=payload).status_code == 202
    resp = client.post("/ingest", json=payload)
    assert resp.status_code == 429
    assert resp.json() == {"detail": "rate_limited", "code": "rate_limited"}
