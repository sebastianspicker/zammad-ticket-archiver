from __future__ import annotations

from fastapi.testclient import TestClient

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.server import create_app


def _admin_settings(storage_root: str):
    return make_settings(
        storage_root,
        overrides={
            "admin": {
                "enabled": True,
                "bearer_token": "admin-token",
                "history_limit": 25,
            }
        },
    )


def test_admin_not_mounted_when_disabled(tmp_path) -> None:
    app = create_app(make_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/admin")
    assert response.status_code == 404


def test_admin_api_requires_bearer_token(tmp_path) -> None:
    app = create_app(_admin_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get("/admin/api/queue/stats")
    assert response.status_code == 401


def test_admin_api_rejects_invalid_bearer_token(tmp_path) -> None:
    app = create_app(_admin_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.get(
        "/admin/api/queue/stats",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert response.status_code == 401


def test_admin_api_accepts_valid_token_and_returns_stats(tmp_path, monkeypatch) -> None:
    app = create_app(_admin_settings(str(tmp_path)))

    import zammad_pdf_archiver.app.routes.admin as admin_route

    async def _stub_stats(_settings):
        return {"execution_backend": "inprocess", "queue_enabled": False}

    monkeypatch.setattr(admin_route, "get_queue_stats", _stub_stats)

    client = TestClient(app)
    response = client.get(
        "/admin/api/queue/stats",
        headers={"Authorization": "Bearer admin-token"},
    )
    assert response.status_code == 200
    assert response.json() == {"execution_backend": "inprocess", "queue_enabled": False}


def test_admin_history_uses_default_history_limit(tmp_path, monkeypatch) -> None:
    app = create_app(_admin_settings(str(tmp_path)))

    import zammad_pdf_archiver.app.routes.admin as admin_route

    called: dict[str, int | None] = {"limit": None}

    async def _stub_history(_settings, *, limit: int, ticket_id: int | None = None):
        called["limit"] = limit
        assert ticket_id is None
        return [{"status": "processed", "ticket_id": 123}]

    monkeypatch.setattr(admin_route, "read_history", _stub_history)

    client = TestClient(app)
    response = client.get(
        "/admin/api/history",
        headers={"Authorization": "Bearer admin-token"},
    )

    assert response.status_code == 200
    assert called["limit"] == 25
    assert response.json()["count"] == 1


def test_admin_retry_dispatches_job(tmp_path, monkeypatch) -> None:
    app = create_app(_admin_settings(str(tmp_path)))

    import zammad_pdf_archiver.app.routes.admin as admin_route

    calls: list[dict[str, object]] = []

    async def _stub_dispatch(*, delivery_id, payload_for_job, settings):
        assert delivery_id is None
        calls.append(payload_for_job)

    monkeypatch.setattr(admin_route, "_dispatch_ticket", _stub_dispatch)

    client = TestClient(app)
    response = client.post(
        "/admin/api/retry/456",
        headers={"Authorization": "Bearer admin-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted", "ticket_id": 456}
    assert len(calls) == 1
    assert calls[0]["ticket_id"] == 456
    assert isinstance(calls[0].get("_request_id"), str)


def test_admin_drain_dlq_bounds_limit(tmp_path, monkeypatch) -> None:
    app = create_app(_admin_settings(str(tmp_path)))

    import zammad_pdf_archiver.app.routes.admin as admin_route

    captured: dict[str, int | None] = {"limit": None}

    async def _stub_drain(_settings, *, limit: int):
        captured["limit"] = limit
        return 7

    monkeypatch.setattr(admin_route, "drain_dlq", _stub_drain)

    client = TestClient(app)
    response = client.post(
        "/admin/api/dlq/drain?limit=999999",
        headers={"Authorization": "Bearer admin-token"},
    )

    assert response.status_code == 200
    assert captured["limit"] == 1000
    assert response.json() == {"status": "ok", "drained": 7}


def test_admin_drain_dlq_returns_503_when_backend_unavailable(tmp_path, monkeypatch) -> None:
    app = create_app(_admin_settings(str(tmp_path)))

    import zammad_pdf_archiver.app.routes.admin as admin_route

    async def _boom(_settings, *, limit: int):  # noqa: ARG001
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(admin_route, "drain_dlq", _boom)

    client = TestClient(app)
    response = client.post(
        "/admin/api/dlq/drain",
        headers={"Authorization": "Bearer admin-token"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "dlq_unavailable"
