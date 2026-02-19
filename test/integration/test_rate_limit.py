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
                "webhook": {"allow_unsigned": True, "allow_unsigned_when_no_secret": True},
            },
        }
    )


def test_rate_limit_triggers_on_ingest(tmp_path) -> None:
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    payload = {"ticket": {"id": 1}}
    assert client.post("/ingest", json=payload).status_code == 202
    assert client.post("/ingest", json=payload).status_code == 202

    resp = client.post("/ingest", json=payload)
    assert resp.status_code == 429
    assert resp.json() == {"detail": "rate_limited", "code": "rate_limited"}
    assert resp.headers.get("X-Request-Id")


def test_rate_limit_key_from_forwarded_header_unit() -> None:
    """Rate limit key can be taken from X-Forwarded-For (unit: _client_key_from_header)."""
    from zammad_pdf_archiver.app.middleware.rate_limit import _client_key, _client_key_from_header

    scope_with_header: dict = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b" 203.0.113.1 , 70.41.3.1 ")],
        "client": ["192.168.1.1", 12345],
    }
    assert _client_key_from_header(scope_with_header, "X-Forwarded-For") == "203.0.113.1"
    assert _client_key(scope_with_header, "X-Forwarded-For") == "203.0.113.1"
    assert _client_key(scope_with_header, None) == "192.168.1.1"
