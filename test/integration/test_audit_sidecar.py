from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime

import httpx
import respx

from zammad_pdf_archiver.adapters.storage.layout import build_filename_from_pattern
from zammad_pdf_archiver.app.jobs import process_ticket as process_ticket_module
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings


def _test_settings(storage_root: str) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
        }
    )


def test_audit_sidecar_written_next_to_pdf_and_matches_sha256(tmp_path, monkeypatch) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)

    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-audit-1",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 123,
                    "number": "20240123",
                    "title": "Example Ticket",
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

        respx.get(
            "https://zammad.example.local/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=["pdf:sign"]))

        respx.get("https://zammad.example.local/api/v1/ticket_articles/by_ticket/123").mock(
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

        respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        respx.post("https://zammad.example.local/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )

        article_route = respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(
                200,
                json={"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
            )
        )

        asyncio.run(process_ticket("delivery-audit-1", payload, settings))

        date_iso = fixed_now.date().isoformat()
        expected_filename = build_filename_from_pattern(
            settings.storage.path_policy.filename_pattern,
            ticket_number="20240123",
            timestamp_utc=date_iso,
        )
        expected_pdf_path = tmp_path / "agent" / "A" / "B" / "C" / expected_filename
        expected_sidecar_path = expected_pdf_path.parent / (expected_pdf_path.name + ".json")

        assert expected_pdf_path.exists()
        assert expected_sidecar_path.exists()

        pdf_bytes = expected_pdf_path.read_bytes()
        sha256_hex = hashlib.sha256(pdf_bytes).hexdigest()

        audit = json.loads(expected_sidecar_path.read_text("utf-8"))
        assert audit["sha256"] == sha256_hex
        assert audit["storage_path"] == str(expected_pdf_path)
        assert audit["ticket_id"] == 123
        assert audit["ticket_number"] == "20240123"

        assert article_route.called
        posted = json.loads(article_route.calls[0].request.content.decode("utf-8"))
        assert sha256_hex in posted["body"]
        assert str(expected_sidecar_path) in posted["body"]
