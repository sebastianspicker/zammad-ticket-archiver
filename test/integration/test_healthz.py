from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _test_settings(storage_root: str) -> Settings:
    return make_settings(storage_root)


def test_healthz_ok(tmp_path) -> None:
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/healthz")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "zammad-pdf-archiver"
    assert isinstance(body["version"], str) and body["version"]
    datetime.fromisoformat(body["time"])

    assert response.headers.get("X-Request-Id")


def test_healthz_omit_version(tmp_path) -> None:
    settings = make_settings(
        str(tmp_path),
        overrides={"observability": {"healthz_omit_version": True}},
    )
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" not in body
    assert "service" not in body
    assert "time" in body
