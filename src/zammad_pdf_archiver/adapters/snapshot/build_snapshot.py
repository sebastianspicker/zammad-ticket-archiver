from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, Protocol

from zammad_pdf_archiver.adapters.zammad.models import Article as ZammadArticle
from zammad_pdf_archiver.adapters.zammad.models import TagList
from zammad_pdf_archiver.adapters.zammad.models import Ticket as ZammadTicket
from zammad_pdf_archiver.domain.html_sanitize import sanitize_html_fragment
from zammad_pdf_archiver.domain.snapshot_models import (
    Article,
    AttachmentMeta,
    PartyRef,
    Snapshot,
    TicketMeta,
)
from zammad_pdf_archiver.domain.ticket_utils import ticket_custom_fields

_HTML_TAG_HINT_RE = re.compile(
    r"<\s*(?:p|div|br|span|a|ul|ol|li|pre|code|blockquote|table|tr|td|th|strong|em|b|i|u)\b",
    re.IGNORECASE,
)


class ZammadSnapshotClient(Protocol):
    async def get_ticket(self, ticket_id: int) -> ZammadTicket: ...

    async def list_tags(self, ticket_id: int) -> TagList: ...

    async def list_articles(self, ticket_id: int) -> list[ZammadArticle]: ...


class ZammadAttachmentClient(Protocol):
    """Client that can fetch attachment binary (for optional PRD §8.2 inclusion)."""

    async def get_attachment_content(
        self, ticket_id: int, article_id: int, attachment_id: int
    ) -> bytes: ...


class _HTMLToText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip_depth += 1
            return
        if tag in {"p", "div", "br", "li", "tr"} and self._skip_depth == 0:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag in {"p", "div", "li", "tr"} and self._skip_depth == 0:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        text = "".join(self._parts)
        # Normalize whitespace without being too opinionated about newlines.
        text = "\n".join(line.strip() for line in text.splitlines())
        text = "\n".join(line for line in text.splitlines() if line)
        return text.strip()


def _strip_html_to_text(html: str) -> str:
    try:
        parser = _HTMLToText()
        parser.feed(html)
        parser.close()
        return parser.get_text()
    except Exception:
        return ""


def _has_html_hint(*, content_type: str | None, body: str) -> bool:
    if content_type and "html" in content_type.lower():
        return True
    # Heuristic: only treat bodies as HTML if they look like common HTML tags.
    return bool(_HTML_TAG_HINT_RE.search(body))


def _party_from_zammad_ref(ref: Any) -> PartyRef | None:
    if ref is None:
        return None
    return PartyRef(
        id=getattr(ref, "id", None),
        login=getattr(ref, "login", None),
        email=getattr(ref, "email", None),
        name=getattr(ref, "name", None),
    )


def _article_to_snapshot(article: ZammadArticle) -> Article:
    body_raw = article.body if isinstance(article.body, str) else ""
    body_html = ""
    body_text = ""

    if body_raw:
        if _has_html_hint(content_type=article.content_type, body=body_raw):
            body_html = sanitize_html_fragment(body_raw)
            if body_html:
                body_text = _strip_html_to_text(body_html)
            else:
                # Bug #P1-1: If sanitization failed, never fallback to raw body as HTML.
                # Fallback to stripped text from raw for body_text; body_html stays empty.
                body_text = _strip_html_to_text(body_raw)
        else:
            body_text = body_raw

    # Best-effort for body_text: if decoding/stripping yielded nothing but we have raw input,
    # keep it as text (will be escaped by Jinja anyway).
    if not body_text and body_raw:
        body_text = body_raw

    attachments: list[AttachmentMeta] = []
    if isinstance(article.attachments, list):
        for att in article.attachments:
            attachment_id = getattr(att, "id", None)
            attachments.append(
                AttachmentMeta(
                    article_id=article.id,
                    attachment_id=attachment_id if isinstance(attachment_id, int) else None,
                    filename=getattr(att, "filename", None),
                    size=getattr(att, "size", None),
                    content_type=getattr(att, "content_type", None),
                )
            )

    return Article(
        id=article.id,
        created_at=article.created_at,
        internal=bool(article.internal) if article.internal is not None else False,
        sender=article.from_ or article.to,
        subject=article.subject,
        body_html=body_html,
        body_text=body_text,
        attachments=attachments,
    )


def _sort_key(article: Article) -> tuple[bool, datetime, int]:
    sentinel = datetime.max.replace(tzinfo=UTC)
    created = article.created_at
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    created = created or sentinel
    return (article.created_at is None, created, article.id)


async def build_snapshot(
    client: ZammadSnapshotClient,
    ticket_id: int,
    *,
    ticket: ZammadTicket | None = None,
    tags: TagList | None = None,
) -> Snapshot:
    if ticket is None:
        ticket = await client.get_ticket(ticket_id)
    if tags is None:
        tags = await client.list_tags(ticket_id)

    articles: list[ZammadArticle] = await client.list_articles(ticket_id)

    snapshot_articles = [_article_to_snapshot(a) for a in articles]
    snapshot_articles.sort(key=_sort_key)

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
            custom_fields=ticket_custom_fields(ticket),
        ),
        articles=snapshot_articles,
    )


async def enrich_attachment_content(
    snapshot: Snapshot,
    client: ZammadAttachmentClient,
    *,
    include_attachment_binary: bool,
    max_attachment_bytes_per_file: int,
    max_total_attachment_bytes: int,
) -> Snapshot:
    """Fetch attachment binaries and set AttachmentMeta.content when within limits (PRD §8.2)."""
    if not _attachment_enrichment_enabled(
        include_attachment_binary=include_attachment_binary,
        max_total_attachment_bytes=max_total_attachment_bytes,
    ):
        return snapshot

    ticket_id = snapshot.ticket.id
    semaphore = asyncio.Semaphore(5)  # Limit concurrency to avoid overloading Zammad

    async def _fetch_one(
        article_id: int, att: AttachmentMeta
    ) -> tuple[int, int | None, bytes | None]:
        if att.attachment_id is None:
            return article_id, None, None

        # Pre-check size if available to avoid useless downloads
        if att.size is not None and att.size > max_attachment_bytes_per_file:
            return article_id, att.attachment_id, None

        async with semaphore:
            try:
                raw = await client.get_attachment_content(
                    ticket_id, article_id, att.attachment_id
                )
                if len(raw) > max_attachment_bytes_per_file:
                    return article_id, att.attachment_id, None
                return article_id, att.attachment_id, raw
            except Exception:
                return article_id, att.attachment_id, None

    targets = _attachment_fetch_targets(snapshot, fetch_one=_fetch_one)

    if not targets:
        return snapshot

    results = await asyncio.gather(*targets)
    content_map = _attachment_content_map(results)
    return _snapshot_with_attachment_content(
        snapshot=snapshot,
        content_map=content_map,
        max_total_attachment_bytes=max_total_attachment_bytes,
    )


def _attachment_enrichment_enabled(
    *,
    include_attachment_binary: bool,
    max_total_attachment_bytes: int,
) -> bool:
    return include_attachment_binary and max_total_attachment_bytes > 0


def _attachment_fetch_targets(
    snapshot: Snapshot,
    *,
    fetch_one,
) -> list[Awaitable[tuple[int, int | None, bytes | None]]]:
    targets: list[Awaitable[tuple[int, int | None, bytes | None]]] = []
    for article in snapshot.articles:
        for att in article.attachments:
            targets.append(fetch_one(article.id, att))
    return targets


def _attachment_content_map(
    results: list[tuple[int, int | None, bytes | None]]
) -> dict[tuple[int, int], bytes]:
    out: dict[tuple[int, int], bytes] = {}
    for article_id, attachment_id, content in results:
        if attachment_id is None or content is None:
            continue
        out[(article_id, attachment_id)] = content
    return out


def _snapshot_with_attachment_content(
    *,
    snapshot: Snapshot,
    content_map: dict[tuple[int, int], bytes],
    max_total_attachment_bytes: int,
) -> Snapshot:
    total_so_far = 0
    new_articles: list[Article] = []
    for article in snapshot.articles:
        new_attachments: list[AttachmentMeta] = []
        for att in article.attachments:
            content, total_so_far = _bounded_content_for_attachment(
                article_id=article.id,
                attachment=att,
                content_map=content_map,
                total_so_far=total_so_far,
                max_total_attachment_bytes=max_total_attachment_bytes,
            )
            new_attachments.append(_copy_attachment(att, content=content))
        new_articles.append(_copy_article(article, attachments=new_attachments))
    return Snapshot(ticket=snapshot.ticket, articles=new_articles)


def _bounded_content_for_attachment(
    *,
    article_id: int,
    attachment: AttachmentMeta,
    content_map: dict[tuple[int, int], bytes],
    total_so_far: int,
    max_total_attachment_bytes: int,
) -> tuple[bytes | None, int]:
    if attachment.attachment_id is None:
        return None, total_so_far
    content = content_map.get((article_id, attachment.attachment_id))
    if not content:
        return None, total_so_far
    if total_so_far + len(content) > max_total_attachment_bytes:
        return None, total_so_far
    return content, total_so_far + len(content)


def _copy_attachment(att: AttachmentMeta, *, content: bytes | None) -> AttachmentMeta:
    return AttachmentMeta(
        article_id=att.article_id,
        attachment_id=att.attachment_id,
        filename=att.filename,
        size=att.size,
        content_type=att.content_type,
        content=content,
    )


def _copy_article(article: Article, *, attachments: list[AttachmentMeta]) -> Article:
    return Article(
        id=article.id,
        created_at=article.created_at,
        internal=article.internal,
        sender=article.sender,
        subject=article.subject,
        body_html=article.body_html,
        body_text=article.body_text,
        attachments=attachments,
    )
