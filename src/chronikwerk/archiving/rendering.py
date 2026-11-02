"""Build normalized snapshots from Zammad tickets and render archive PDFs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING, Any, Literal

import structlog

from chronikwerk.archiving.snapshot import build_snapshot
from chronikwerk.concurrency import run_sync_cancellation_safe
from chronikwerk.documents.models import Snapshot
from chronikwerk.documents.options import DocumentOptions
from chronikwerk.documents.pdf import render_pdf
from chronikwerk.documents.signing import sign_pdf_with_provenance
from chronikwerk.operations.metrics import render_seconds, sign_seconds

if TYPE_CHECKING:
    from chronikwerk.zammad.dto import Article, TagList, Ticket

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ReadingCopies:
    """Carry the reading copies rendered from one snapshot."""

    archival: bytes | None  # PDF/A-2u, PAdES-signed when signing is enabled
    accessible: bytes | None  # PDF/UA-1, never signed
    snapshot: Snapshot
    signing_cert_fingerprint: str | None


async def _build_snapshot_for(
    *,
    ticket_id: int,
    ticket: Ticket,
    tags: TagList,
    custom_fields: dict[str, Any],
    options: DocumentOptions,
    articles: Sequence[Article],
) -> Snapshot:
    snapshot = await build_snapshot(
        ticket=ticket,
        tags=tags,
        custom_fields=custom_fields,
        max_articles=options.max_articles,
        article_limit_mode=options.article_limit_mode,
        articles=articles,
    )
    if snapshot.articles_omitted:
        log.warning(
            "process_ticket.article_limit_capped",
            ticket_id=ticket_id,
            total=snapshot.articles_total,
            cap=len(snapshot.articles),
        )
    return snapshot


async def _sign_if_enabled(pdf_bytes: bytes, options: DocumentOptions) -> tuple[bytes, str | None]:
    if not options.signing.enabled:
        return pdf_bytes, None
    sign_started = perf_counter()
    signed_pdf = await run_sync_cancellation_safe(
        sign_pdf_with_provenance,
        pdf_bytes,
        signing=options.signing,
    )
    sign_seconds.observe(perf_counter() - sign_started)
    return signed_pdf.pdf_bytes, signed_pdf.certificate_fingerprint


async def render_reading_copies(
    *,
    ticket_id: int,
    ticket: Ticket,
    tags: TagList,
    custom_fields: dict[str, Any],
    options: DocumentOptions,
    variants: Sequence[Literal["pdf/a-2u", "pdf/ua-1"]],
    articles: Sequence[Article],
) -> ReadingCopies:
    """Render the requested reading copies from one snapshot; sign only the archival copy."""
    snapshot = await _build_snapshot_for(
        ticket_id=ticket_id,
        ticket=ticket,
        tags=tags,
        custom_fields=custom_fields,
        options=options,
        articles=articles,
    )

    rendered: dict[str, bytes] = {}
    for variant in dict.fromkeys(variants):
        render_started = perf_counter()
        rendered[variant] = await render_pdf(
            snapshot,
            max_articles=options.max_articles,
            locale=options.locale,
            timezone=options.timezone,
            pdf_variant=variant,
        )
        render_seconds.observe(perf_counter() - render_started)

    archival = rendered.get("pdf/a-2u")
    signing_cert_fingerprint = None
    if archival is not None:
        archival, signing_cert_fingerprint = await _sign_if_enabled(archival, options)
    return ReadingCopies(
        archival=archival,
        accessible=rendered.get("pdf/ua-1"),
        snapshot=snapshot,
        signing_cert_fingerprint=signing_cert_fingerprint,
    )
