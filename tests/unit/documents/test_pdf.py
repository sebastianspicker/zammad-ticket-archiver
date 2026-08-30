"""Characterize the public PDF renderer output contract."""

from __future__ import annotations

import asyncio

from chronikwerk.documents.models import Snapshot, TicketMeta
from chronikwerk.documents.pdf import render_pdf


def test_render_pdf_produces_a_tagged_pdf_document() -> None:
    snapshot = Snapshot(ticket=TicketMeta(id=1, number="T1", title="PDF contract"), articles=[])

    pdf_bytes = asyncio.run(render_pdf(snapshot, locale="en-GB"))

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 5_000
