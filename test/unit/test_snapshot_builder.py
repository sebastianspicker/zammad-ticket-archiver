from __future__ import annotations

import asyncio

from zammad_pdf_archiver.adapters.snapshot.build_snapshot import (
    build_snapshot,
    enrich_attachment_content,
)
from zammad_pdf_archiver.adapters.zammad.models import Article as ZammadArticle
from zammad_pdf_archiver.domain.snapshot_models import (
    Article,
    AttachmentMeta,
    Snapshot,
    TicketMeta,
)
from zammad_pdf_archiver.adapters.zammad.models import TagList
from zammad_pdf_archiver.adapters.zammad.models import Ticket as ZammadTicket


class _FakeZammadClient:
    def __init__(
        self,
        *,
        ticket: ZammadTicket,
        tags: list[str],
        articles: list[ZammadArticle],
    ) -> None:
        self._ticket = ticket
        self._tags = tags
        self._articles = articles

    async def get_ticket(self, _: int) -> ZammadTicket:
        return self._ticket

    async def list_tags(self, _: int) -> TagList:
        return TagList(self._tags)

    async def list_articles(self, _: int) -> list[ZammadArticle]:
        return self._articles


def test_articles_are_sorted_chronologically() -> None:
    async def run() -> None:
        ticket = ZammadTicket.model_validate({"id": 1, "number": "T1"})
        articles = [
            ZammadArticle.model_validate(
                {"id": 2, "created_at": "2024-01-02T00:00:00Z", "body": "later"}
            ),
            ZammadArticle.model_validate(
                {"id": 1, "created_at": "2024-01-01T00:00:00Z", "body": "earlier"}
            ),
        ]
        client = _FakeZammadClient(ticket=ticket, tags=[], articles=articles)

        snapshot = await build_snapshot(client, 1)
        assert [a.id for a in snapshot.articles] == [1, 2]

    asyncio.run(run())


def test_internal_flag_maps_none_to_false() -> None:
    async def run() -> None:
        ticket = ZammadTicket.model_validate({"id": 1, "number": "T1"})
        articles = [
            ZammadArticle.model_validate(
                {"id": 1, "created_at": "2024-01-01T00:00:00Z", "internal": None, "body": "x"}
            )
        ]
        client = _FakeZammadClient(ticket=ticket, tags=[], articles=articles)

        snapshot = await build_snapshot(client, 1)
        assert snapshot.articles[0].internal is False

    asyncio.run(run())


def test_html_is_stripped_to_text_and_falls_back_to_body() -> None:
    async def run() -> None:
        ticket = ZammadTicket.model_validate({"id": 1, "number": "T1"})
        articles = [
            ZammadArticle.model_validate(
                {
                    "id": 1,
                    "created_at": "2024-01-01T00:00:00Z",
                    "content_type": "text/html",
                    "body": "<p>Hello <b>World</b></p>",
                }
            ),
            ZammadArticle.model_validate(
                {
                    "id": 2,
                    "created_at": "2024-01-02T00:00:00Z",
                    "content_type": "text/html",
                    "body": "<p><br/></p>",
                }
            ),
        ]
        client = _FakeZammadClient(ticket=ticket, tags=[], articles=articles)

        snapshot = await build_snapshot(client, 1)
        assert snapshot.articles[0].body_text == "Hello World"
        assert snapshot.articles[1].body_text == "<p><br/></p>"

    asyncio.run(run())


def test_attachment_metadata_extraction_is_robust() -> None:
    async def run() -> None:
        ticket = ZammadTicket.model_validate({"id": 1, "number": "T1"})
        articles = [
            ZammadArticle.model_validate(
                {
                    "id": 1,
                    "created_at": "2024-01-01T00:00:00Z",
                    "attachments": [
                        {"id": 10, "filename": "a.txt", "size": 123, "content_type": "text/plain"},
                        {"filename": "missing-id.bin"},
                    ],
                    "body": "x",
                }
            )
        ]
        client = _FakeZammadClient(ticket=ticket, tags=[], articles=articles)

        snapshot = await build_snapshot(client, 1)
        assert len(snapshot.articles[0].attachments) == 2
        assert snapshot.articles[0].attachments[0].article_id == 1
        assert snapshot.articles[0].attachments[0].attachment_id == 10
        assert snapshot.articles[0].attachments[1].attachment_id is None
        assert snapshot.articles[0].attachments[1].filename == "missing-id.bin"

    asyncio.run(run())


def test_body_html_is_sanitized_for_safe_pdf_rendering() -> None:
    async def run() -> None:
        ticket = ZammadTicket.model_validate({"id": 1, "number": "T1"})
        articles = [
            ZammadArticle.model_validate(
                {
                    "id": 1,
                    "created_at": "2024-01-01T00:00:00Z",
                    "content_type": "text/html",
                    "body": (
                        '<p onclick="x">Hello '
                        "<script>alert(1)</script>"
                        '<a href="javascript:alert(1)">bad</a> '
                        '<a href="https://example.com">ok</a>'
                        "</p>"
                    ),
                }
            )
        ]
        client = _FakeZammadClient(ticket=ticket, tags=[], articles=articles)

        snapshot = await build_snapshot(client, 1)
        body_html = snapshot.articles[0].body_html
        assert "<script" not in body_html
        assert "onclick" not in body_html
        assert "javascript:" not in body_html
        assert 'href="https://example.com"' in body_html

    asyncio.run(run())


def test_plain_text_with_angle_brackets_is_not_treated_as_html() -> None:
    async def run() -> None:
        ticket = ZammadTicket.model_validate({"id": 1, "number": "T1"})
        articles = [
            ZammadArticle.model_validate(
                {
                    "id": 1,
                    "created_at": "2024-01-01T00:00:00Z",
                    "content_type": "text/plain",
                    "body": "Please include <foo> in the config.",
                }
            )
        ]
        client = _FakeZammadClient(ticket=ticket, tags=[], articles=articles)

        snapshot = await build_snapshot(client, 1)
        assert snapshot.articles[0].body_html == ""
        assert snapshot.articles[0].body_text == "Please include <foo> in the config."

    asyncio.run(run())


def test_enrich_attachment_content_unchanged_when_disabled() -> None:
    """When include_attachment_binary is False, snapshot is returned unchanged (PRD §8.2)."""
    snapshot = Snapshot(
        ticket=TicketMeta(id=1, number="T1", title="t"),
        articles=[
            Article(
                id=1,
                body_html="",
                body_text="",
                attachments=[
                    AttachmentMeta(article_id=1, attachment_id=10, filename="a.txt", size=5),
                ],
            )
        ],
    )
    result = asyncio.run(
        enrich_attachment_content(
            snapshot,
            type("Client", (), {"get_attachment_content": lambda *a: None})(),
            include_attachment_binary=False,
            max_attachment_bytes_per_file=1000,
            max_total_attachment_bytes=5000,
        )
    )
    assert result.articles[0].attachments[0].content is None


async def _run_enrich_fills_content() -> None:
    class FakeAttachmentClient:
        async def get_attachment_content(
            self, ticket_id: int, article_id: int, attachment_id: int
        ) -> bytes:
            return b"binary data"

    snapshot = Snapshot(
        ticket=TicketMeta(id=1, number="T1", title="t"),
        articles=[
            Article(
                id=1,
                body_html="",
                body_text="",
                attachments=[
                    AttachmentMeta(article_id=1, attachment_id=10, filename="a.txt", size=11),
                ],
            )
        ],
    )
    result = await enrich_attachment_content(
        snapshot,
        FakeAttachmentClient(),
        include_attachment_binary=True,
        max_attachment_bytes_per_file=100,
        max_total_attachment_bytes=1000,
    )
    assert result.articles[0].attachments[0].content == b"binary data"


def test_enrich_attachment_content_fills_content_when_enabled() -> None:
    """When include_attachment_binary is True and within limits, content is set (PRD §8.2)."""
    asyncio.run(_run_enrich_fills_content())
