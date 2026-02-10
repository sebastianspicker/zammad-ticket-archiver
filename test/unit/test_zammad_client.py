from __future__ import annotations

import asyncio

import httpx
import pytest
import respx

from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient
from zammad_pdf_archiver.adapters.zammad.errors import AuthError, NotFoundError, ServerError


async def _no_sleep(_: float) -> None:
    return None


def test_get_ticket_success() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            sleep=_no_sleep,
        ) as client:
            ticket = await client.get_ticket(123)
            assert ticket.id == 123
            assert ticket.number == "20240123"
            assert ticket.owner is not None
            assert ticket.owner.login == "agent"

    with respx.mock:
        respx.get("https://zammad.example/api/v1/tickets/123").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 123,
                    "number": "20240123",
                    "title": "Example ticket",
                    "owner": {"login": "agent"},
                    "updated_by": {"login": "agent"},
                    "customer": {"login": "customer"},
                    "created_at": "2024-01-01T00:00:00Z",
                    "updated_at": "2024-01-02T00:00:00Z",
                    "preferences": {"custom_fields": {"archive_path": "/mnt/archive"}},
                    "ignored_field": "extra",
                },
            )
        )
        asyncio.run(run())


def test_list_tags_success() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            sleep=_no_sleep,
        ) as client:
            tags = await client.list_tags(123)
            assert tags.root == ["pdf:sign", "archived"]

    with respx.mock:
        respx.get(
            "https://zammad.example/api/v1/tags",
            params={"object": "Ticket", "o_id": "123"},
        ).mock(return_value=httpx.Response(200, json=["pdf:sign", "archived"]))
        asyncio.run(run())


def test_add_tag_success() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            sleep=_no_sleep,
        ) as client:
            await client.add_tag(123, "archived")

    with respx.mock:
        route = respx.post("https://zammad.example/api/v1/tags/add").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        asyncio.run(run())
        assert route.called


def test_remove_tag_success() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            sleep=_no_sleep,
        ) as client:
            await client.remove_tag(123, "archived")

    with respx.mock:
        route = respx.post("https://zammad.example/api/v1/tags/remove").mock(
            return_value=httpx.Response(200, json={"success": True})
        )
        asyncio.run(run())
        assert route.called


def test_create_internal_article_success() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            sleep=_no_sleep,
        ) as client:
            article = await client.create_internal_article(123, "Subject", "<p>Body</p>")
            assert article.id == 999
            assert article.internal is True
            assert article.subject == "Subject"
            assert article.body == "<p>Body</p>"

    with respx.mock:
        route = respx.post("https://zammad.example/api/v1/ticket_articles").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": 999,
                    "internal": True,
                    "subject": "Subject",
                    "body": "<p>Body</p>",
                    "content_type": "text/html",
                    "created_at": "2024-01-02T00:00:00Z",
                },
            )
        )
        asyncio.run(run())
        assert route.called


def test_get_attachment_content_success() -> None:
    """get_attachment_content returns raw bytes from ticket_attachment endpoint (PRD §8.2)."""
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            sleep=_no_sleep,
        ) as client:
            data = await client.get_attachment_content(1, 2, 3)
            assert data == b"binary content"

    with respx.mock:
        respx.get(
            "https://zammad.example/api/v1/ticket_attachment/1/2/3",
            headers={"Accept": "*/*"},
        ).mock(return_value=httpx.Response(200, content=b"binary content"))
        asyncio.run(run())


def test_list_articles_success() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            sleep=_no_sleep,
        ) as client:
            articles = await client.list_articles(123)
            assert [a.id for a in articles] == [1, 2]
            assert articles[0].from_ == "agent@example.com"

    with respx.mock:
        respx.get("https://zammad.example/api/v1/ticket_articles/by_ticket/123").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": 1,
                        "created_at": "2024-01-01T00:00:00Z",
                        "internal": False,
                        "subject": "Hello",
                        "body": "Body",
                        "content_type": "text/plain",
                        "from": "agent@example.com",
                        "to": "support@example.com",
                        "attachments": [{"id": 10, "filename": "a.txt", "size": 123}],
                    },
                    {
                        "id": 2,
                        "created_at": "2024-01-02T00:00:00Z",
                        "internal": True,
                        "subject": "Note",
                        "body": "Internal",
                        "content_type": "text/plain",
                    },
                ],
            )
        )
        asyncio.run(run())


def test_401_raises_auth_error() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="bad-token",
            sleep=_no_sleep,
        ) as client:
            with pytest.raises(AuthError):
                await client.get_ticket(123)

    with respx.mock:
        respx.get("https://zammad.example/api/v1/tickets/123").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )
        asyncio.run(run())


def test_404_raises_not_found_error() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            sleep=_no_sleep,
        ) as client:
            with pytest.raises(NotFoundError):
                await client.get_ticket(404)

    with respx.mock:
        respx.get("https://zammad.example/api/v1/tickets/404").mock(
            return_value=httpx.Response(404, json={"error": "not found"})
        )
        asyncio.run(run())


def test_5xx_raises_server_error_after_retries() -> None:
    async def run() -> None:
        async with AsyncZammadClient(
            base_url="https://zammad.example",
            api_token="test-token",
            sleep=_no_sleep,
        ) as client:
            with pytest.raises(ServerError):
                await client.get_ticket(123)

    with respx.mock:
        route = respx.get("https://zammad.example/api/v1/tickets/123").mock(
            side_effect=[
                httpx.Response(500, json={"error": "boom"}),
                httpx.Response(502, json={"error": "boom"}),
                httpx.Response(503, json={"error": "boom"}),
                httpx.Response(500, json={"error": "boom"}),
            ]
        )
        asyncio.run(run())
        assert route.call_count == 4
