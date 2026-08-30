"""Build normalized snapshots from Zammad tickets and render archive PDFs."""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING

import structlog

from chronikwerk.documents.models import Snapshot
from chronikwerk.documents.options import DocumentOptions
from chronikwerk.documents.pdf import render_pdf
from chronikwerk.documents.signing import sign_pdf_with_provenance
from chronikwerk.documents.snapshot import build_snapshot
from chronikwerk.operations.async_work import run_sync_cancellation_safe
from chronikwerk.operations.metrics import render_seconds, sign_seconds

if TYPE_CHECKING:
    from chronikwerk.zammad.dto import TagList, Ticket
    from chronikwerk.zammad.gateway import AsyncZammadClient

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RenderedTicket:
    """Bundle rendered bytes with the normalized snapshot and signing provenance."""

    pdf_bytes: bytes
    snapshot: Snapshot
    signing_cert_fingerprint: str | None


def _cap_articles_if_configured(
    snapshot: Snapshot,
    *,
    ticket_id: int,
    options: DocumentOptions,
) -> Snapshot:
    max_articles = options.max_articles
    if options.article_limit_mode == "cap_and_continue" and 0 < max_articles < len(
        snapshot.articles
    ):
        log.warning(
            "process_ticket.article_limit_capped",
            ticket_id=ticket_id,
            total=len(snapshot.articles),
            cap=max_articles,
        )
        total = snapshot.articles_total
        if total is None:
            total = len(snapshot.articles)
        return snapshot.model_copy(
            update={
                "articles": snapshot.articles[:max_articles],
                "articles_total": total,
                "articles_omitted": total - max_articles,
            }
        )
    return snapshot


async def build_and_render_pdf(
    *,
    client: AsyncZammadClient,
    ticket_id: int,
    ticket: Ticket,
    tags: TagList,
    options: DocumentOptions,
) -> RenderedTicket:
    """Fetch render inputs and produce PDF bytes for one ticket."""
    snapshot = await build_snapshot(client, ticket_id, ticket=ticket, tags=tags)
    snapshot = _cap_articles_if_configured(
        snapshot,
        ticket_id=ticket_id,
        options=options,
    )

    render_started = perf_counter()
    pdf_bytes = await render_pdf(
        snapshot,
        max_articles=options.max_articles,
        locale=options.locale,
        timezone=options.timezone,
    )
    render_seconds.observe(perf_counter() - render_started)

    signing_cert_fingerprint = None
    if options.signing.enabled:
        sign_started = perf_counter()
        signed_pdf = await run_sync_cancellation_safe(
            sign_pdf_with_provenance,
            pdf_bytes,
            signing=options.signing,
        )
        pdf_bytes = signed_pdf.pdf_bytes
        signing_cert_fingerprint = signed_pdf.certificate_fingerprint
        sign_seconds.observe(perf_counter() - sign_started)

    return RenderedTicket(
        pdf_bytes=pdf_bytes,
        snapshot=snapshot,
        signing_cert_fingerprint=signing_cert_fingerprint,
    )
