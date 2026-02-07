from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime

import httpx
import respx

from zammad_pdf_archiver.app.server import create_app
from zammad_pdf_archiver.config.load import load_settings
from zammad_pdf_archiver.domain.state_machine import (
    DONE_TAG,
    ERROR_TAG,
    PROCESSING_TAG,
    TRIGGER_TAG,
)


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    return f"sha1={digest}"


def _called_tag_items(route: respx.Route) -> list[str]:
    items: list[str] = []
    for call in route.calls:
        body = json.loads(call.request.content.decode("utf-8"))
        items.append(body.get("item"))
    return items


def test_e2e_smoke_ingest_happy_path_writes_pdf_and_updates_zammad(tmp_path, monkeypatch) -> None:
    secret = "test-secret"
    monkeypatch.setenv("ZAMMAD_BASE_URL", "https://zammad.example.local")
    monkeypatch.setenv("ZAMMAD_API_TOKEN", "test-token")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("WEBHOOK_HMAC_SECRET", secret)

    settings = load_settings()
    app = create_app(settings)

    import zammad_pdf_archiver.app.jobs.process_ticket as process_ticket_module

    process_ticket_module._DELIVERY_ID_SETS.clear()
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)

    payload = {"ticket": {"id": 123}, "user": {"login": "agent-from-webhook"}}
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    with respx.mock(assert_all_called=True) as zammad:
        ticket_route = zammad.get("https://zammad.example.local/api/v1/tickets/123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 123,
                    "number": "20240123",
                    "owner": {"login": "agent"},
                    "updated_by": {"login": "fallback-agent"},
                    "preferences": {
                        "custom_fields": {
                            "archive_user_mode": "owner",
                            "archive_path": ["A", "B", "C"],
                        }
                    },
                },
            )
        )

        tags_route = zammad.get(
            "https://zammad.example.local/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=[TRIGGER_TAG]))

        zammad.get("https://zammad.example.local/api/v1/ticket_articles/by_ticket/123").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "created_at": "2026-02-07T11:59:00Z",
                        "internal": False,
                        "subject": "Hello",
                        "body": "<p>Hello World</p>",
                        "content_type": "text/html",
                        "from": "customer@example.invalid",
                        "attachments": [],
                    }
                ],
            )
        )

        remove_tag_route = zammad.post("https://zammad.example.local/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        add_tag_route = zammad.post("https://zammad.example.local/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )

        article_route = zammad.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(
                200,
                json={"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
            )
        )

        async def _call_ingest() -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/ingest",
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Hub-Signature": _sign(body, secret),
                        "X-Zammad-Delivery": "delivery-smoke-e2e-20260207-0001",
                    },
                )

        response = asyncio.run(_call_ingest())

        assert response.status_code == 202
        assert response.json() == {"accepted": True, "ticket_id": 123}

        date_iso = fixed_now.date().isoformat()
        expected_path = (
            tmp_path / "agent" / "A" / "B" / "C" / f"Ticket-20240123_{date_iso}.pdf"
        )
        assert expected_path.exists()
        assert expected_path.read_bytes().startswith(b"%PDF")

        assert ticket_route.called
        assert tags_route.called
        assert article_route.called

        added = _called_tag_items(add_tag_route)
        removed = _called_tag_items(remove_tag_route)

        assert PROCESSING_TAG in added
        assert DONE_TAG in added
        assert ERROR_TAG not in added

        assert TRIGGER_TAG in removed
        assert DONE_TAG in removed
        assert ERROR_TAG in removed
        assert PROCESSING_TAG in removed


def test_e2e_smoke_ingest_duplicate_delivery_id_is_idempotent(tmp_path, monkeypatch) -> None:
    secret = "test-secret"
    monkeypatch.setenv("ZAMMAD_BASE_URL", "https://zammad.example.local")
    monkeypatch.setenv("ZAMMAD_API_TOKEN", "test-token")
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("WEBHOOK_HMAC_SECRET", secret)

    settings = load_settings()
    app = create_app(settings)

    import zammad_pdf_archiver.app.jobs.process_ticket as process_ticket_module

    process_ticket_module._DELIVERY_ID_SETS.clear()
    process_ticket_module._IN_FLIGHT_TICKETS.clear()
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)

    payload = {"ticket": {"id": 123}, "user": {"login": "agent-from-webhook"}}
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")

    with respx.mock(assert_all_called=True) as zammad:
        ticket_route = zammad.get("https://zammad.example.local/api/v1/tickets/123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 123,
                    "number": "20240123",
                    "owner": {"login": "agent"},
                    "updated_by": {"login": "fallback-agent"},
                    "preferences": {
                        "custom_fields": {
                            "archive_user_mode": "owner",
                            "archive_path": ["A", "B", "C"],
                        }
                    },
                },
            )
        )

        tags_route = zammad.get(
            "https://zammad.example.local/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=[TRIGGER_TAG]))

        zammad.get("https://zammad.example.local/api/v1/ticket_articles/by_ticket/123").mock(
            return_value=httpx.Response(200, json=[])
        )
        zammad.post("https://zammad.example.local/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        zammad.post("https://zammad.example.local/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        article_route = zammad.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(200, json={"id": 999})
        )

        async def _call_ingest(delivery_id: str) -> httpx.Response:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/ingest",
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Hub-Signature": _sign(body, secret),
                        "X-Zammad-Delivery": delivery_id,
                    },
                )

        first = asyncio.run(_call_ingest("delivery-smoke-dedupe-1"))
        second = asyncio.run(_call_ingest("delivery-smoke-dedupe-1"))

        assert first.status_code == 202
        assert second.status_code == 202
        assert first.json() == {"accepted": True, "ticket_id": 123}
        assert second.json() == {"accepted": True, "ticket_id": 123}

        assert ticket_route.call_count == 1
        assert tags_route.call_count == 1
        assert article_route.call_count == 1
