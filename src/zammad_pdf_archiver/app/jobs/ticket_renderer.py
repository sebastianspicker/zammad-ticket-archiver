"""Ticket rendering operations - handles PDF generation and signing.

This module provides functions for rendering tickets to PDF,
including optional signing and timestamping.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import TYPE_CHECKING

import structlog

from zammad_pdf_archiver.adapters.pdf.render_pdf import render_pdf
from zammad_pdf_archiver.adapters.signing.sign_pdf import sign_pdf
from zammad_pdf_archiver.adapters.snapshot.build_snapshot import (
    build_snapshot,
    enrich_attachment_content,
)
from zammad_pdf_archiver.domain.snapshot_models import Snapshot
from zammad_pdf_archiver.observability.metrics import render_seconds, sign_seconds

if TYPE_CHECKING:
    from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient
    from zammad_pdf_archiver.adapters.zammad.models import TagList, Ticket
    from zammad_pdf_archiver.config.settings import Settings

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RenderResult:
    """Result of PDF rendering operation."""
    pdf_bytes: bytes
    snapshot: Snapshot


async def build_and_render_pdf(
    client: AsyncZammadClient,
    ticket: Ticket,
    tags: TagList,
    ticket_id: int,
    settings: Settings,
) -> RenderResult:
    """Build snapshot and render PDF with optional signing.
    
    This function:
    1. Builds a normalized snapshot from ticket data
    2. Optionally caps articles if configured
    3. Enriches with attachment content if configured
    4. Renders PDF using Jinja2 + WeasyPrint
    5. Optionally signs with PAdES and timestamps with RFC3161
    
    Args:
        client: Zammad API client
        ticket: Ticket object
        tags: Tag list
        ticket_id: Ticket ID
        settings: Application settings
        
    Returns:
        RenderResult with PDF bytes and snapshot
    """
    snapshot = await build_snapshot(
        client,
        ticket_id,
        ticket=ticket,
        tags=tags,
    )
    
    # Handle article limit capping
    max_articles = settings.pdf.max_articles
    if (
        getattr(settings.pdf, "article_limit_mode", "fail") == "cap_and_continue"
        and max_articles > 0
        and len(snapshot.articles) > max_articles
    ):
        log.warning(
            "process_ticket.article_limit_capped",
            ticket_id=ticket_id,
            total=len(snapshot.articles),
            cap=max_articles,
        )
        snapshot = Snapshot(
            ticket=snapshot.ticket,
            articles=snapshot.articles[:max_articles],
        )
    
    # Enrich with attachment binaries if configured
    snapshot = await enrich_attachment_content(
        snapshot,
        client,
        include_attachment_binary=settings.pdf.include_attachment_binary,
        max_attachment_bytes_per_file=settings.pdf.max_attachment_bytes_per_file,
        max_total_attachment_bytes=settings.pdf.max_total_attachment_bytes,
    )
    
    # Render PDF
    render_start = perf_counter()
    pdf_bytes = render_pdf(
        snapshot,
        settings.pdf.template,
        max_articles=settings.pdf.max_articles,
        locale=settings.pdf.locale,
        timezone=settings.pdf.timezone,
        templates_root=settings.pdf.templates_root,
    )
    render_seconds.observe(perf_counter() - render_start)
    
    # Sign if enabled
    if settings.signing.enabled:
        sign_start = perf_counter()
        # pyHanko's synchronous signing helper uses asyncio.run() internally.
        # Offload to a worker thread to avoid:
        # "asyncio.run() cannot be called from a running event loop".
        pdf_bytes = await asyncio.to_thread(sign_pdf, pdf_bytes, settings)
        sign_seconds.observe(perf_counter() - sign_start)
    
    return RenderResult(pdf_bytes=pdf_bytes, snapshot=snapshot)
