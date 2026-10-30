"""Characterize the public PDF renderer output contract."""

from __future__ import annotations

import asyncio
import io

from pyhanko.pdf_utils.reader import PdfFileReader

from chronikwerk.documents.models import Snapshot, TicketMeta
from chronikwerk.documents.pdf import render_pdf


def test_render_pdf_produces_a_tagged_pdf_document() -> None:
    snapshot = Snapshot(ticket=TicketMeta(id=1, number="T1", title="PDF contract"), articles=[])

    pdf_bytes = asyncio.run(render_pdf(snapshot, locale="en-GB"))

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) > 5_000


def pdf_xmp(pdf_bytes: bytes) -> str:
    """Return the document-level XMP packet of a PDF."""
    reader = PdfFileReader(io.BytesIO(pdf_bytes))
    return reader.root["/Metadata"].data.decode("utf-8")


def is_tagged(pdf_bytes: bytes) -> bool:
    """Report whether the PDF catalog carries a structure tree."""
    return "/StructTreeRoot" in PdfFileReader(io.BytesIO(pdf_bytes)).root


def _minimal_snapshot() -> Snapshot:
    """Return a snapshot with a ticket and no articles."""
    return Snapshot(ticket=TicketMeta(id=1, number="T1", title="PDF contract"), articles=[])


def test_archival_variant_declares_pdfa_2u_and_is_tagged() -> None:
    pdf_bytes = asyncio.run(render_pdf(_minimal_snapshot(), locale="en-GB", pdf_variant="pdf/a-2u"))

    xmp = pdf_xmp(pdf_bytes)
    assert 'pdfaid:part="2"' in xmp
    assert 'pdfaid:conformance="U"' in xmp
    assert "pdfuaid" not in xmp
    assert is_tagged(pdf_bytes)


def test_accessible_variant_declares_pdfua_1() -> None:
    pdf_bytes = asyncio.run(render_pdf(_minimal_snapshot(), locale="en-GB", pdf_variant="pdf/ua-1"))

    xmp = pdf_xmp(pdf_bytes)
    assert 'pdfuaid:part="1"' in xmp
    assert "pdfaid" not in xmp


def test_default_variant_is_unchanged_pdfua_1_output() -> None:
    snapshot = _minimal_snapshot()

    default = asyncio.run(render_pdf(snapshot, locale="en-GB"))
    explicit = asyncio.run(render_pdf(snapshot, locale="en-GB", pdf_variant="pdf/ua-1"))

    assert default == explicit
