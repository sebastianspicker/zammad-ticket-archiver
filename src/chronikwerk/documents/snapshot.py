"""Build an immutable document snapshot from Zammad ticket resources."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from html import escape
from html.parser import HTMLParser
from typing import TYPE_CHECKING, Any

from chronikwerk.documents.models import (
    Article,
    AttachmentMeta,
    PartyRef,
    Snapshot,
    TicketMeta,
)
from chronikwerk.documents.sanitize import sanitize_html_fragment
from chronikwerk.zammad.dto import Article as ZammadArticle
from chronikwerk.zammad.dto import TagList
from chronikwerk.zammad.dto import Ticket as ZammadTicket

if TYPE_CHECKING:
    from chronikwerk.zammad.gateway import AsyncZammadClient


def _party_from_zammad_ref(ref: Any) -> PartyRef | None:
    if ref is None:
        return None
    return PartyRef(
        id=getattr(ref, "id", None),
        login=getattr(ref, "login", None),
        email=getattr(ref, "email", None),
        name=getattr(ref, "name", None),
    )


class _BodyTextExtractor(HTMLParser):
    _BLOCK_TAGS = frozenset({"br", "div", "li", "p", "pre", "tr"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Capture source markup while normalizing extracted text boundaries."""
        if tag.lower() in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        """Close the current source element in the lightweight extraction parser."""
        if tag.lower() in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        """Append textual content while preserving meaningful spacing."""
        self.parts.append(data)


def _readable_text_from_html(value: str) -> str:
    parser = _BodyTextExtractor()
    parser.feed(value)
    parser.close()
    lines = [" ".join(line.split()) for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _article_body_html_and_text(article: ZammadArticle) -> tuple[str, str]:
    body_raw = article.body if isinstance(article.body, str) else ""
    content_type = (article.content_type or "").lower()
    looks_like_html = bool(re.search(r"<\s*/?\s*[a-z][^>]*>", body_raw, re.IGNORECASE))
    if "html" not in content_type and not looks_like_html:
        return escape(body_raw, quote=False), body_raw

    sanitized = sanitize_html_fragment(body_raw)
    if sanitized:
        return sanitized, _readable_text_from_html(sanitized)
    # Fail closed: escaped source is safe for the template fallback and keeps
    # the original text available when malformed HTML cannot be sanitized.
    return escape(body_raw, quote=False), body_raw


def _attachment_to_meta(article: ZammadArticle, attachment: Any) -> AttachmentMeta:
    attachment_id = getattr(attachment, "id", None)
    return AttachmentMeta(
        article_id=article.id,
        attachment_id=attachment_id if isinstance(attachment_id, int) else None,
        filename=getattr(attachment, "filename", None),
        size=getattr(attachment, "size", None),
        content_type=getattr(attachment, "content_type", None),
    )


def _article_attachments(article: ZammadArticle) -> list[AttachmentMeta]:
    if not isinstance(article.attachments, list):
        return []
    return [_attachment_to_meta(article, attachment) for attachment in article.attachments]


def _article_to_snapshot(article: ZammadArticle) -> Article:
    body_html, body_text = _article_body_html_and_text(article)
    return Article(
        id=article.id,
        created_at=article.created_at,
        internal=bool(article.internal) if article.internal is not None else False,
        sender=article.from_ or article.to,
        subject=article.subject,
        body_html=body_html,
        body_text=body_text,
        attachments=_article_attachments(article),
    )


def _sort_key(article: Article) -> tuple[bool, datetime, int]:
    sentinel = datetime.max.replace(tzinfo=UTC)
    created = article.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    created = created or sentinel
    return (article.created_at is None, created, article.id)


async def build_snapshot(
    client: AsyncZammadClient,
    ticket_id: int,
    *,
    ticket: ZammadTicket | None = None,
    tags: TagList | None = None,
) -> Snapshot:
    """Normalize fetched Zammad resources into the immutable rendering input."""
    if ticket is None:
        ticket = await client.get_ticket(ticket_id)
    if tags is None:
        tags = await client.list_tags(ticket_id)

    articles = await client.list_articles(ticket_id)
    snapshot_articles = [_article_to_snapshot(article) for article in articles]
    snapshot_articles.sort(key=_sort_key)

    custom_fields = (
        ticket.preferences.custom_fields
        if ticket.preferences is not None and isinstance(ticket.preferences.custom_fields, dict)
        else {}
    )

    return Snapshot(
        ticket=TicketMeta(
            id=ticket.id,
            number=ticket.number,
            title=ticket.title,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
            customer=_party_from_zammad_ref(ticket.customer),
            owner=_party_from_zammad_ref(ticket.owner),
            tags=list(tags.root),
            custom_fields=custom_fields,
        ),
        articles=snapshot_articles,
        articles_total=len(snapshot_articles),
    )
