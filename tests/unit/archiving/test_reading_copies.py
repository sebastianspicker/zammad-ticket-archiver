"""Characterize rendering the archival and accessible reading copies from one snapshot."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import chronikwerk.archiving.rendering as rendering
from chronikwerk.archiving.rendering import ReadingCopies, render_reading_copies
from chronikwerk.documents.models import Snapshot, TicketMeta
from chronikwerk.documents.options import DocumentOptions, SigningOptions, TimestampOptions
from tests.support.pki import PFX_PASSWORD, build_pki, write_pkcs12
from tests.unit.documents.test_pdf import is_tagged, pdf_xmp


def _options(*, pfx_path: Path | None = None) -> DocumentOptions:
    """Build document options, enabling signing only when a PFX path is given."""
    return DocumentOptions(
        max_articles=0,
        article_limit_mode="render_all",
        locale="en-GB",
        timezone="UTC",
        signing=SigningOptions(
            enabled=pfx_path is not None,
            pfx_path=pfx_path,
            pfx_password=PFX_PASSWORD if pfx_path is not None else None,
            reason="Archive",
            location="Datacenter",
            timestamp=TimestampOptions(
                enabled=False,
                tsa_url=None,
                timeout_seconds=5.0,
                ca_bundle_path=None,
                user=None,
                password=None,
                trust_env=False,
                allow_insecure_http=False,
                allow_private_networks=False,
            ),
        ),
    )


def _install_snapshot_spy(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Replace snapshot construction with a minimal snapshot and record each call."""
    calls: list[int] = []

    async def build_snapshot(_client: object, ticket_id: int, **_kwargs: Any) -> Snapshot:
        calls.append(ticket_id)
        return Snapshot(ticket=TicketMeta(id=ticket_id, number="T1", title="Copies"), articles=[])

    monkeypatch.setattr(rendering, "build_snapshot", build_snapshot)
    return calls


def _render(options: DocumentOptions, variants: tuple[Any, ...]) -> ReadingCopies:
    """Render the requested reading copies through the public entry point."""
    return asyncio.run(
        render_reading_copies(
            client=object(),  # type: ignore[arg-type]
            ticket_id=42,
            ticket=object(),  # type: ignore[arg-type]
            tags=object(),  # type: ignore[arg-type]
            custom_fields={},
            options=options,
            variants=variants,
        )
    )


def test_both_variants_render_from_one_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshot_calls = _install_snapshot_spy(monkeypatch)

    copies = _render(_options(), ("pdf/a-2u", "pdf/ua-1"))

    assert snapshot_calls == [42]
    assert copies.snapshot.ticket.id == 42
    assert copies.signing_cert_fingerprint is None
    assert copies.archival is not None
    assert copies.accessible is not None
    archival_xmp = pdf_xmp(copies.archival)
    assert 'pdfaid:part="2"' in archival_xmp
    assert 'pdfaid:conformance="U"' in archival_xmp
    assert is_tagged(copies.archival)
    assert 'pdfuaid:part="1"' in pdf_xmp(copies.accessible)


def test_only_requested_variants_are_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_snapshot_spy(monkeypatch)

    archival_only = _render(_options(), ("pdf/a-2u",))
    accessible_only = _render(_options(), ("pdf/ua-1",))

    assert archival_only.archival is not None
    assert archival_only.accessible is None
    assert accessible_only.archival is None
    assert accessible_only.accessible is not None


def test_signing_applies_to_the_archival_copy_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_snapshot_spy(monkeypatch)
    pki = build_pki()
    pfx_path = write_pkcs12(tmp_path, pki.seal, (pki.root.certificate,))

    copies = _render(_options(pfx_path=pfx_path), ("pdf/a-2u", "pdf/ua-1"))

    assert copies.archival is not None
    assert copies.accessible is not None
    assert b"/ByteRange" in copies.archival
    assert b"/ByteRange" not in copies.accessible
    assert copies.signing_cert_fingerprint is not None
    assert 'pdfaid:part="2"' in pdf_xmp(copies.archival)


def test_accessible_only_request_is_never_signed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_snapshot_spy(monkeypatch)
    pki = build_pki()
    pfx_path = write_pkcs12(tmp_path, pki.seal, (pki.root.certificate,))

    copies = _render(_options(pfx_path=pfx_path), ("pdf/ua-1",))

    assert copies.accessible is not None
    assert b"/ByteRange" not in copies.accessible
    assert copies.signing_cert_fingerprint is None
