from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.settings import Settings


def _test_settings(storage_root: str) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
            "hardening": {"webhook": {"allow_unsigned": True}},
        }
    )


def _test_settings_require_delivery_id(storage_root: str) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
            "workflow": {"delivery_id_ttl_seconds": 3600},
            "hardening": {"webhook": {"allow_unsigned": True, "require_delivery_id": True}},
        }
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
    assert response.json() == {"accepted": True, "ticket_id": 123}
    assert response.headers.get("X-Request-Id")
    assert len(calls) == 1


def test_ingest_rejects_payload_without_ticket_id(tmp_path) -> None:
    """Schema validation: payload must contain ticket.id or ticket_id (422)."""
    app = create_app(_test_settings(str(tmp_path)))
    client = TestClient(app)

    response = client.post("/ingest", json={})
    assert response.status_code == 422


def test_request_id_header_is_preserved(tmp_path) -> None:
    app = create_app(_test_settings(str(tmp_path)))
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
    assert response.json() == {"detail": "missing_delivery_id"}
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
