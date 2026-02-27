from __future__ import annotations

from fastapi.testclient import TestClient

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app


def _ops_auth_settings(storage_root: str):
    return make_settings(
        storage_root,
        overrides={"admin": {"bearer_token": "ops-token"}},
    )


def test_jobs_history_endpoint_returns_items(tmp_path, monkeypatch) -> None:
    app = create_app(_ops_auth_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.jobs as jobs_route

    async def _stub_history(_settings, *, limit: int, ticket_id: int | None = None):
        assert limit == 50
        assert ticket_id == 123
        return [{"status": "processed", "ticket_id": 123}]

    monkeypatch.setattr(jobs_route, "read_history", _stub_history)

    client = TestClient(app)
    response = client.get(
        "/jobs/history?limit=50&ticket_id=123",
        headers={"Authorization": "Bearer ops-token"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "count": 1,
        "items": [{"status": "processed", "ticket_id": 123}],
    }


def test_jobs_dlq_drain_endpoint_bounds_limit(tmp_path, monkeypatch) -> None:
    app = create_app(_ops_auth_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.jobs as jobs_route

    captured: dict[str, int | None] = {"limit": None}

    async def _stub_drain(_settings, *, limit: int):
        captured["limit"] = limit
        return 4

    monkeypatch.setattr(jobs_route, "drain_dlq", _stub_drain)

    client = TestClient(app)
    response = client.post(
        "/jobs/queue/dlq/drain?limit=2000",
        headers={"Authorization": "Bearer ops-token"},
    )
    assert response.status_code == 200
    assert captured["limit"] == 1000
    assert response.json() == {"status": "ok", "drained": 4}


def test_jobs_history_requires_bearer_token(tmp_path) -> None:
    app = create_app(_ops_auth_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/jobs/history")
    assert response.status_code == 401


def test_jobs_dlq_drain_requires_bearer_token(tmp_path) -> None:
    app = create_app(_ops_auth_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.post("/jobs/queue/dlq/drain")
    assert response.status_code == 401


def test_jobs_history_requires_configured_ops_token(tmp_path) -> None:
    app = create_app(make_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/jobs/history")
    assert response.status_code == 503
    assert response.json()["detail"] == "ops_token_not_configured"


def test_jobs_dlq_drain_returns_503_on_backend_error(tmp_path, monkeypatch) -> None:
    app = create_app(_ops_auth_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.jobs as jobs_route

    async def _boom(_settings, *, limit: int):  # noqa: ARG001
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(jobs_route, "drain_dlq", _boom)

    client = TestClient(app)
    response = client.post(
        "/jobs/queue/dlq/drain",
        headers={"Authorization": "Bearer ops-token"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "dlq_unavailable"
