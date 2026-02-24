from __future__ import annotations

import asyncio
import errno
import json
from datetime import UTC, datetime

import httpx
import respx

from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.adapters.storage.layout import build_filename_from_pattern
from zammad_pdf_archiver.app.jobs import process_ticket as process_ticket_module
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.state_machine import (
    DONE_TAG,
    ERROR_TAG,
    PROCESSING_TAG,
    TRIGGER_TAG,
)


def _test_settings(storage_root: str) -> Settings:
    return Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": storage_root},
        }
    )


def _called_tag_items(route: respx.Route) -> list[str]:
    items: list[str] = []
    for call in route.calls:
        body = json.loads(call.request.content.decode("utf-8"))
        items.append(body.get("item"))
    return items


def test_process_ticket_v01_happy_path_writes_pdf_and_updates_tags(tmp_path, monkeypatch) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)
    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-123",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        ticket_route = respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
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
                            "archive_path": "A > B > C",
                        }
                    },
                },
            )
        )

        tags_route = respx.get(
            "https://zammad.example.local/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=[TRIGGER_TAG]))

        articles_route = respx.get(
            "https://zammad.example.local/api/v1/ticket_articles/by_ticket/123"
        ).mock(
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

        remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )

        article_route = respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(
                200,
                json={"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
            )
        )

        asyncio.run(process_ticket("delivery-happy-1", payload, settings))

        # Idempotency: same delivery id should be skipped entirely.
        asyncio.run(process_ticket("delivery-happy-1", payload, settings))

        assert ticket_route.call_count == 1
        assert tags_route.call_count == 1

        # File written in the expected directory.
        date_iso = fixed_now.date().isoformat()
        expected_filename = build_filename_from_pattern(
            settings.storage.path_policy.filename_pattern,
            ticket_number="20240123",
            timestamp_utc=date_iso,
        )
        expected_path = tmp_path / "agent" / "A" / "B" / "C" / expected_filename

        assert expected_path.exists()
        written = expected_path.read_bytes()
        assert written.startswith(b"%PDF")
        assert b"archived at" not in written

        assert articles_route.called
        added = _called_tag_items(add_tag_route)
        removed = _called_tag_items(remove_tag_route)

        assert PROCESSING_TAG in added
        assert DONE_TAG in added
        assert ERROR_TAG not in added

        assert TRIGGER_TAG in removed
        assert DONE_TAG in removed
        assert ERROR_TAG in removed
        assert PROCESSING_TAG in removed

        assert article_route.called
        req = json.loads(article_route.calls[0].request.content.decode("utf-8"))
        assert req["ticket_id"] == 123
        assert f"PDF archived ({VERSION})" in req["subject"]
        assert str(expected_path.parent) in req["body"]
        assert expected_path.name in req["body"]
        assert str(len(written)) in req["body"]
        assert "req-123" in req["body"]


def test_process_ticket_v01_failure_sets_error_tag_and_posts_note(tmp_path, monkeypatch) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)
    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-err-1",
        "user": {"login": "agent-from-webhook"},
    }

    def _boom(*_args, **_kwargs) -> None:
        raise PermissionError("no-write")

    monkeypatch.setattr(process_ticket_module, "store_ticket_files", _boom)

    with respx.mock:
        respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
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

        respx.get(
            "https://zammad.example.local/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=[TRIGGER_TAG]))

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

        remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )

        article_route = respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(
                200,
                json={"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
            )
        )

        asyncio.run(process_ticket("delivery-err-1", payload, settings))

        date_iso = fixed_now.date().isoformat()
        expected_filename = build_filename_from_pattern(
            settings.storage.path_policy.filename_pattern,
            ticket_number="20240123",
            timestamp_utc=date_iso,
        )
        expected_path = tmp_path / "agent" / "A" / "B" / "C" / expected_filename
        assert not expected_path.exists()

        added = _called_tag_items(add_tag_route)
        removed = _called_tag_items(remove_tag_route)

        assert PROCESSING_TAG in added
        assert DONE_TAG not in added
        assert TRIGGER_TAG not in added  # permanent: drop trigger to prevent loops
        assert ERROR_TAG in added

        assert PROCESSING_TAG in removed  # removed during apply_error/best-effort cleanup

        assert article_route.called
        req = json.loads(article_route.calls[0].request.content.decode("utf-8"))
        assert f"PDF archiver error ({VERSION})" in req["subject"]
        assert "Permanent" in req["body"]
        assert "PermissionError" in req["body"]


def test_process_ticket_v01_transient_failure_keeps_trigger_and_posts_note(
    tmp_path, monkeypatch
) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)
    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-err-transient-1",
        "user": {"login": "agent-from-webhook"},
    }

    def _boom(*_args, **_kwargs) -> None:
        raise OSError(errno.EAGAIN, "try again")

    monkeypatch.setattr(process_ticket_module, "store_ticket_files", _boom)

    with respx.mock:
        respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
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

        respx.get(
            "https://zammad.example.local/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=[TRIGGER_TAG]))

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

        remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )

        article_route = respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(
                200,
                json={"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
            )
        )

        asyncio.run(process_ticket("delivery-err-transient-1", payload, settings))

        added = _called_tag_items(add_tag_route)
        removed = _called_tag_items(remove_tag_route)

        assert PROCESSING_TAG in added
        assert DONE_TAG not in added
        assert TRIGGER_TAG in added  # transient: keep trigger for retries
        assert ERROR_TAG in added

        assert PROCESSING_TAG in removed

        assert article_route.called
        req = json.loads(article_route.calls[0].request.content.decode("utf-8"))
        assert f"PDF archiver error ({VERSION})" in req["subject"]
        assert "Transient" in req["body"]


def test_process_ticket_v01_invalid_archive_path_is_permanent_and_writes_no_files(
    tmp_path, monkeypatch
) -> None:
    settings = _test_settings(str(tmp_path))
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)
    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-path-invalid-1",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
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
                            "archive_path": ["A", "..", "C"],
                        }
                    },
                },
            )
        )

        respx.get(
            "https://zammad.example.local/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=[TRIGGER_TAG]))

        remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        article_route = respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(200, json={"id": 999})
        )

        asyncio.run(process_ticket("delivery-path-invalid-1", payload, settings))

        assert list(tmp_path.rglob("*.pdf")) == []
        assert list(tmp_path.rglob("*.pdf.json")) == []

        added = _called_tag_items(add_tag_route)
        removed = _called_tag_items(remove_tag_route)

        assert PROCESSING_TAG in added
        assert DONE_TAG not in added
        assert TRIGGER_TAG not in added
        assert ERROR_TAG in added

        assert PROCESSING_TAG in removed
        assert TRIGGER_TAG in removed

        assert article_route.called
        req = json.loads(article_route.calls[0].request.content.decode("utf-8"))
        assert f"PDF archiver error ({VERSION})" in req["subject"]
        assert "Permanent" in req["body"]
        assert "ValueError" in req["body"]


def test_process_ticket_v01_enforces_pdf_max_articles_setting(tmp_path, monkeypatch) -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": str(tmp_path)},
            "pdf": {"max_articles": 1},
        }
    )
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)
    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-max-articles-1",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
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

        respx.get(
            "https://zammad.example.local/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=[TRIGGER_TAG]))

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
                    ,
                    {
                        "id": 2,
                        "created_at": "2026-02-07T11:59:30Z",
                        "internal": False,
                        "subject": "World",
                        "body": "<p>World Hello</p>",
                        "content_type": "text/html",
                        "from": "customer@example.invalid",
                        "attachments": [],
                    },
                ],
            )
        )

        remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        article_route = respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(200, json={"id": 999})
        )

        asyncio.run(process_ticket("delivery-max-articles-1", payload, settings))

        removed = _called_tag_items(remove_tag_route)
        added = _called_tag_items(add_tag_route)

        assert TRIGGER_TAG in removed
        assert PROCESSING_TAG in added
        assert ERROR_TAG in added
        assert DONE_TAG not in added

        assert article_route.called
        req = json.loads(article_route.calls[0].request.content.decode("utf-8"))
        assert "Permanent" in req["body"]
        assert "too many articles" in req["body"]


def test_process_ticket_v01_pdf_max_articles_zero_disables_limit(tmp_path, monkeypatch) -> None:
    settings = Settings.from_mapping(
        {
            "zammad": {"base_url": "https://zammad.example.local", "api_token": "test-token"},
            "storage": {"root": str(tmp_path)},
            "pdf": {"max_articles": 0},
        }
    )
    fixed_now = datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC)
    monkeypatch.setattr(process_ticket_module, "_now_utc", lambda: fixed_now)
    payload = {
        "ticket": {"id": 123},
        "_request_id": "req-max-articles-disabled",
        "user": {"login": "agent-from-webhook"},
    }

    with respx.mock:
        respx.get("https://zammad.example.local/api/v1/tickets/123").mock(
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

        respx.get(
            "https://zammad.example.local/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=[TRIGGER_TAG]))

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
                    },
                    {
                        "id": 2,
                        "created_at": "2026-02-07T11:59:30Z",
                        "internal": False,
                        "subject": "World",
                        "body": "<p>World Hello</p>",
                        "content_type": "text/html",
                        "from": "customer@example.invalid",
                        "attachments": [],
                    },
                ],
            )
        )

        remove_tag_route = respx.post("https://zammad.example.local/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        add_tag_route = respx.post("https://zammad.example.local/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        article_route = respx.post("https://zammad.example.local/api/v1/ticket_articles").mock(
            return_value=httpx.Response(
                200,
                json={"id": 999, "internal": True, "subject": "ok", "body": "<p>ok</p>"},
            )
        )

        asyncio.run(process_ticket("delivery-max-articles-disabled", payload, settings))

        removed = _called_tag_items(remove_tag_route)
        added = _called_tag_items(add_tag_route)

        assert TRIGGER_TAG in removed
        assert PROCESSING_TAG in added
        assert DONE_TAG in added
        assert ERROR_TAG not in added

        assert article_route.called
