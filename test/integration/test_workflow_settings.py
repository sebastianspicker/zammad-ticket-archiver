from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

import httpx
import respx

from zammad_pdf_archiver.adapters.storage.layout import build_filename_from_pattern
from zammad_pdf_archiver.app.jobs import process_ticket as process_ticket_module
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.state_machine import DONE_TAG, ERROR_TAG, PROCESSING_TAG


def _settings(storage_root: str, *, workflow: dict | None = None) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
            "workflow": workflow or {},
        }
    )


def _called_tag_items(route: respx.Route) -> list[str]:
    items: list[str] = []
    for call in route.calls:
        body = json.loads(call.request.content.decode("utf-8"))
        items.append(body.get("item"))
    return items


def _mock_ticket(*, ticket_id: int = 123) -> None:
    respx.get(f"https://zammad.example.local/api/v1/tickets/{ticket_id}").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": ticket_id,
                "number": "20240123",
                "owner": {"login": "agent"},
                "updated_by": {"login": "fallback-agent"},
                "preferences": {
                    "custom_fields": {
                        "archive_user_mode": "owner",
                        "archive_path": "A > B > C",
                    }
                },
            },
        )
    )


def _mock_articles(*, ticket_id: int = 123) -> None:
    respx.get(f"https://zammad.example.local/api/v1/ticket_articles/by_ticket/{ticket_id}").mock(
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


def _mock_tag_routes() -> tuple[respx.Route, respx.Route]:
    remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
        return_value=httpx.Response(200, json={"success": True})
    )
    return remove_tag_route, add_tag_route


def test_workflow_trigger_tag_is_respected(tmp_path, monkeypatch) -> None:
    settings = _settings(str(tmp_path), workflow={"trigger_tag": "pdf:archive"})
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)

    payload = {"ticket": {"id": 123}, "_request_id": "req-workflow-1"}

    with respx.mock:
        _mock_ticket(ticket_id=123)
        _mock_articles(ticket_id=123)

        respx.get(
            "https://zammad.example.local/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=["pdf:archive"]))

        remove_tag_route, add_tag_route = _mock_tag_routes()

        respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(
                200,
                json={"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
            )
        )

        asyncio.run(process_ticket("delivery-workflow-1", payload, settings))

        removed = _called_tag_items(remove_tag_route)
        added = _called_tag_items(add_tag_route)

        assert "pdf:archive" in removed
        assert PROCESSING_TAG in added
        assert DONE_TAG in added
        assert ERROR_TAG not in added

        date_iso = fixed_now.date().isoformat()
        expected_filename = build_filename_from_pattern(
            settings.storage.path_policy.filename_pattern,
            ticket_number="20240123",
            timestamp_utc=date_iso,
        )
        expected_pdf_path = tmp_path / "agent" / "A" / "B" / "C" / expected_filename
        assert expected_pdf_path.exists()


def test_workflow_require_tag_can_be_disabled(tmp_path, monkeypatch) -> None:
    settings = _settings(str(tmp_path), workflow={"require_tag": False})
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)

    payload = {"ticket": {"id": 123}, "_request_id": "req-workflow-2"}

    with respx.mock:
        _mock_ticket(ticket_id=123)
        _mock_articles(ticket_id=123)

        respx.get(
            "https://zammad.example.local/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=[]))

        _mock_tag_routes()

        respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(
                200,
                json={"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
            )
        )

        asyncio.run(process_ticket("delivery-workflow-2", payload, settings))

        date_iso = fixed_now.date().isoformat()
        expected_filename = build_filename_from_pattern(
            settings.storage.path_policy.filename_pattern,
            ticket_number="20240123",
            timestamp_utc=date_iso,
        )
        expected_pdf_path = tmp_path / "agent" / "A" / "B" / "C" / expected_filename
        assert expected_pdf_path.exists()


def test_workflow_acknowledge_on_success_can_be_disabled(tmp_path, monkeypatch) -> None:
    settings = _settings(str(tmp_path), workflow={"acknowledge_on_success": False})
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)

    payload = {"ticket": {"id": 123}, "_request_id": "req-workflow-3"}

    with respx.mock:
        _mock_ticket(ticket_id=123)
        _mock_articles(ticket_id=123)

        respx.get(
            "https://zammad.example.local/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=["pdf:sign"]))

        _mock_tag_routes()

        article_route = respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(
                200,
                json={"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
            )
        )

        asyncio.run(process_ticket("delivery-workflow-3", payload, settings))

        assert article_route.call_count == 0
