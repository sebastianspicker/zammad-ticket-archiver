"""Characterize normalized snapshot ordering, sanitization, and attachment metadata."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from chronikwerk.documents.snapshot import build_snapshot
from chronikwerk.zammad.dto import Article as ZammadArticle
from chronikwerk.zammad.dto import TagList, Ticket


class SnapshotClient:
    """Return controlled Zammad resources through the snapshot gateway shape."""

    def __init__(self, *, ticket: Ticket, articles: list[ZammadArticle]) -> None:
        self.ticket = ticket
        self.articles = articles

    async def get_ticket(self, _ticket_id: int) -> Ticket:
        return self.ticket

    async def list_tags(self, _ticket_id: int) -> TagList:
        return TagList(["pdf:sign"])

    async def list_articles(self, _ticket_id: int) -> list[ZammadArticle]:
        return self.articles


def test_snapshot_sanitizes_sorts_and_retains_attachment_metadata() -> None:
    articles = [
        ZammadArticle.model_validate(
            {
                "id": 2,
                "created_at": "2024-01-02T00:00:00Z",
                "body": "later",
            }
        ),
        ZammadArticle.model_validate(
            {
                "id": 1,
                "created_at": "2024-01-01T00:00:00Z",
                "body": "<b>earlier</b><script>discard()</script>",
                "attachments": [
                    {
                        "id": 10,
                        "filename": "a.txt",
                        "size": 123,
                        "content_type": "text/plain",
                    }
                ],
            }
        ),
    ]
    client = SnapshotClient(ticket=Ticket(id=1, number="T1"), articles=articles)

    snapshot = asyncio.run(build_snapshot(cast(Any, client), 1))

    assert [article.id for article in snapshot.articles] == [1, 2]
    assert snapshot.articles[0].body_html == "<b>earlier</b>"
    assert snapshot.articles[0].body_text == "earlier"
    assert snapshot.articles[0].attachments[0].model_dump() == {
        "article_id": 1,
        "attachment_id": 10,
        "filename": "a.txt",
        "size": 123,
        "content_type": "text/plain",
    }
