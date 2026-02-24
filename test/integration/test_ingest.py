from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient

from test.support.settings_factory import make_settings
from zammad_pdf_archiver.app.jobs import ticket_stores
from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _test_settings(storage_root: str) -> Settings:
    return make_settings(storage_root)


def _test_settings_require_delivery_id(storage_root: str) -> Settings:
    return make_settings(
        storage_root,
        require_delivery_id=True,
        overrides={"workflow": {"delivery_id_ttl_seconds": 3600}},
    )


def test_ingest_accepts_and_extracts_ticket_id(tmp_path, monkeypatch) -> None:
    calls: list[tuple[object, object, object]] = []

    async def _stub_process_ticket(delivery_id, payload, settings) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = client.post("/ingest", json={"ticket": {"id": 123}})
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "ticket_id": 123}
    assert response.headers.get("X-Request-Id")
    assert len(calls) == 1


def test_ingest_rejects_payload_without_ticket_id(tmp_path) -> None:
    """Schema validation: payload must contain ticket.id or ticket_id (422)."""
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.post("/ingest", json={})
    assert response.status_code == 422


def test_request_id_header_is_preserved(tmp_path, monkeypatch) -> None:
    async def _stub_process_ticket(delivery_id, payload, settings) -> None:  # noqa: ANN001, ARG001
        return None

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = client.post(
        "/ingest",
        json={"ticket": {"id": 1}},
        headers={"X-Request-Id": "test-req-id"},
    )
    assert response.status_code == 202
    assert response.headers["X-Request-Id"] == "test-req-id"


def test_ingest_passes_delivery_id_header_to_process_ticket(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_process_ticket(
        delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = client.post(
        "/ingest",
        json={"ticket": {"id": 123}},
        headers={"X-Zammad-Delivery": "delivery-xyz"},
    )
    assert response.status_code == 202
    assert len(calls) == 1

    delivery_id, payload, _settings = calls[0]
    assert delivery_id == "delivery-xyz"
    assert payload["ticket"]["id"] == 123
    assert isinstance(payload.get("_request_id"), str)
    assert payload["_request_id"]


def test_ingest_rejects_missing_delivery_id_when_required(tmp_path) -> None:
    app = create_app(_test_settings_require_delivery_id(str(tmp_path)))
    client = TestClient(app)

    response = client.post("/ingest", json={"ticket": {"id": 123}})
    assert response.status_code == 400
    assert response.json() == {"detail": "missing_delivery_id", "code": "missing_delivery_id"}
    assert response.headers.get("X-Request-Id")


def test_ingest_rejects_invalid_ticket_id_type(tmp_path, monkeypatch) -> None:
    """Schema validation: ticket.id must be a positive int (422); no background run."""
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_process_ticket(
        delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = client.post("/ingest", json={"ticket": {"id": True}})
    assert response.status_code == 422
    assert calls == []


def test_ingest_batch_accepts_multiple_payloads(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_process_ticket(
        delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = client.post(
        "/ingest/batch",
        json=[
            {"ticket": {"id": 111}},
            {"ticket_id": 222},
        ],
    )
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "count": 2}
    assert len(calls) == 2
    assert calls[0][0] is None
    assert calls[1][0] is None
    assert calls[0][1]["ticket"]["id"] == 111
    assert calls[1][1]["ticket_id"] == 222


def test_retry_endpoint_accepts_ticket_id(tmp_path, monkeypatch) -> None:
    calls: list[tuple[str | None, dict[str, Any], Settings]] = []

    async def _stub_process_ticket(
        delivery_id: str | None, payload: dict[str, Any], settings: Settings
    ) -> None:
        calls.append((delivery_id, payload, settings))

    app = create_app(_test_settings(str(tmp_path)))
    import zammad_pdf_archiver.app.routes.ingest as ingest_route

    monkeypatch.setattr(ingest_route, "process_ticket", _stub_process_ticket)
    client = TestClient(app)

    response = client.post("/retry/987")
    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "ticket_id": 987}
    assert len(calls) == 1
    assert calls[0][0] is None
    assert calls[0][1]["ticket_id"] == 987


def test_jobs_endpoint_reports_in_flight_status(tmp_path) -> None:
    ticket_stores.reset_for_tests()
    settings = _test_settings(str(tmp_path))
    app = create_app(settings)
    client = TestClient(app)

    acquired = asyncio.run(ticket_stores.try_acquire_ticket(settings, 404))
    assert acquired is True
    try:
        response = client.get("/jobs/404")
        assert response.status_code == 200
        assert response.json() == {"ticket_id": 404, "in_flight": True, "shutting_down": False}
    finally:
        asyncio.run(ticket_stores.release_ticket(settings, 404))
        ticket_stores.reset_for_tests()
