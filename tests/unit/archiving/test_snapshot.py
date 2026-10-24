"""Characterize normalized snapshot ordering, sanitization, and attachment metadata."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from chronikwerk.archiving.snapshot import build_snapshot
from chronikwerk.zammad.dto import Article as ZammadArticle
from chronikwerk.zammad.dto import TagList, Ticket
from tests.support.zammad_fixtures import load_zammad_fixture


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
    assert snapshot.articles_total == 2
    assert snapshot.articles_omitted == 0


def test_snapshot_cap_keeps_the_earliest_articles_and_reports_omissions() -> None:
    """Capping keeps the earliest k articles by time and leaves no trace of the omitted ones."""
    articles = [
        ZammadArticle(
            id=number,
            created_at=datetime(2024, 1, number, tzinfo=UTC),
            body=f"<b>body-{number}</b>",
            attachments=[],
        )
        for number in (5, 2, 4, 1, 3)
    ]
    client = SnapshotClient(ticket=Ticket(id=1, number="T1"), articles=articles)

    snapshot = asyncio.run(
        build_snapshot(
            cast(Any, client),
            1,
            max_articles=2,
            article_limit_mode="cap_and_continue",
        )
    )

    assert [article.id for article in snapshot.articles] == [1, 2]
    assert snapshot.articles[0].body_html == "<b>body-1</b>"
    assert snapshot.articles_total == 5
    assert snapshot.articles_omitted == 3
    serialized = snapshot.model_dump_json()
    assert all(f"body-{number}" not in serialized for number in (3, 4, 5))


def test_snapshot_rejects_oversized_article_list_in_fail_mode() -> None:
    """The default fail mode refuses a ticket with more articles than the configured maximum."""
    articles = [ZammadArticle(id=2), ZammadArticle(id=1)]
    client = SnapshotClient(ticket=Ticket(id=1, number="T1"), articles=articles)

    with pytest.raises(
        ValueError,
        match=r"too many articles: ticket has 2 articles; maximum allowed is 1",
    ):
        asyncio.run(build_snapshot(cast(Any, client), 1, max_articles=1))


@pytest.mark.parametrize("limit", [None, 0, 4, 2])
def test_snapshot_limits_preserve_naive_missing_and_tied_timestamp_order(limit) -> None:
    articles = [
        ZammadArticle(id=1, body="missing"),
        ZammadArticle(id=4, created_at=datetime(2024, 1, 1), body="naive"),
        ZammadArticle(id=3, created_at=datetime(2024, 1, 1, tzinfo=UTC), body="aware"),
        ZammadArticle(id=2, body="missing second"),
    ]
    client = SnapshotClient(ticket=Ticket(id=1, number="T1"), articles=articles)
    snapshot = asyncio.run(
        build_snapshot(
            cast(Any, client), 1, max_articles=limit, article_limit_mode="cap_and_continue"
        )
    )
    expected = [3, 4] if limit == 2 else [3, 4, 1, 2]
    assert [article.id for article in snapshot.articles] == expected
    assert snapshot.articles_total == 4
    assert snapshot.articles_omitted == 4 - len(expected)


def test_exact_article_limit_in_fail_mode_includes_every_article() -> None:
    articles = [ZammadArticle(id=2), ZammadArticle(id=1)]
    client = SnapshotClient(ticket=Ticket(id=1, number="T1"), articles=articles)
    snapshot = asyncio.run(build_snapshot(cast(Any, client), 1, max_articles=2))
    assert [article.id for article in snapshot.articles] == [1, 2]
    assert snapshot.articles_total == 2
    assert snapshot.articles_omitted == 0


def test_snapshot_reads_attachment_mime_type_from_zammad_preferences() -> None:
    articles = [
        ZammadArticle.model_validate(article)
        for article in load_zammad_fixture("ticket_articles_by_ticket.json")
    ]
    client = SnapshotClient(ticket=Ticket(id=42, number="31042"), articles=articles)

    snapshot = asyncio.run(build_snapshot(cast(Any, client), 42))

    (attachment,) = snapshot.articles[0].attachments
    assert (attachment.filename, attachment.size, attachment.content_type) == (
        "error.png",
        29935,
        "image/png",
    )


@pytest.mark.parametrize(
    ("attachment", "expected"),
    [
        ({"id": 1, "content_type": " text/plain "}, "text/plain"),
        ({"id": 1, "preferences": {"Mime-Type": "application/pdf"}}, "application/pdf"),
        ({"id": 1, "preferences": {"Content-Type": "  "}}, None),
    ],
)
def test_snapshot_normalizes_attachment_mime_type_sources(
    attachment: dict[str, Any], expected: str | None
) -> None:
    article = ZammadArticle.model_validate({"id": 1, "body": "x", "attachments": [attachment]})
    client = SnapshotClient(ticket=Ticket(id=1, number="T1"), articles=[article])

    snapshot = asyncio.run(build_snapshot(cast(Any, client), 1))

    assert snapshot.articles[0].attachments[0].content_type == expected
