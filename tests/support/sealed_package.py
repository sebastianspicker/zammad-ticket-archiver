"""Build sealed, timestamped packages with the synthetic PKI for verification tests."""

from __future__ import annotations

import asyncio
import io
from datetime import UTC, datetime
from pathlib import Path

from asn1crypto import x509 as asn1_x509
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.writer import PdfFileWriter
from pyhanko.sign.timestamps.dummy_client import DummyTimeStamper
from pyhanko_certvalidator.registry import SimpleCertificateStore

from chronikwerk.documents.options import SigningOptions, TimestampOptions
from chronikwerk.documents.signing import sign_pdf_with_provenance
from chronikwerk.preservation.manifest import (
    SEAL_NAME,
    TOKEN_NAME,
    Coverage,
    CoverageCount,
    FileRole,
    OriginalEmailCoverage,
    Producer,
    TicketRef,
)
from chronikwerk.preservation.package import PackageBuilder
from chronikwerk.preservation.seal import (
    SealOptions,
    load_trust_anchors,
    seal_manifest,
    timestamp_seal,
)
from tests.support.pki import (
    PFX_PASSWORD,
    asn1_certificate,
    asn1_private_key,
    build_pki,
    pem_bundle,
    write_pkcs12,
)


def timestamper() -> DummyTimeStamper:
    """Return an in-process timestamper backed by the synthetic TSA identity."""
    pki = build_pki()
    return DummyTimeStamper(
        tsa_cert=asn1_certificate(pki.tsa.certificate),
        tsa_key=asn1_private_key(pki.tsa.key),
        certs_to_embed=SimpleCertificateStore.from_certs([asn1_certificate(pki.root.certificate)]),
    )


def seal_options(directory: Path) -> SealOptions:
    """Write a PFX for the seal identity below ``directory`` and return seal options."""
    pki = build_pki()
    path = write_pkcs12(directory, pki.seal, (pki.root.certificate,))
    return SealOptions(pfx_path=path, pfx_password=PFX_PASSWORD)


def write_anchors(directory: Path, *, trusted: bool = True) -> Path:
    """Write the synthetic root (or an unrelated root) as a PEM trust anchor bundle."""
    pki = build_pki()
    root = pki.root if trusted else pki.unrelated_root
    path = directory / ("anchors.pem" if trusted else "other-anchors.pem")
    path.write_bytes(pem_bundle(root.certificate))
    return path


def load_anchors(directory: Path, *, trusted: bool = True) -> tuple[asn1_x509.Certificate, ...]:
    """Write and load trust anchors in one step."""
    return load_trust_anchors(write_anchors(directory, trusted=trusted))


def blank_pdf() -> bytes:
    """Return a one-page unsigned PDF."""
    writer = PdfFileWriter()
    box = generic.ArrayObject([generic.NumberObject(value) for value in (0, 0, 100, 100)])
    writer.insert_page(
        generic.DictionaryObject(
            {
                generic.pdf_name("/Type"): generic.pdf_name("/Page"),
                generic.pdf_name("/MediaBox"): box,
                generic.pdf_name("/Resources"): generic.DictionaryObject(),
            }
        )
    )
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def signed_pdf(directory: Path) -> bytes:
    """Return a PAdES-signed PDF created with the production signing helper."""
    pki = build_pki()
    pfx = write_pkcs12(directory, pki.seal, (pki.root.certificate,), name="pdf.pfx")
    options = SigningOptions(
        enabled=True,
        pfx_path=pfx,
        pfx_password=PFX_PASSWORD,
        reason="Archive",
        location="Test",
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
    )
    return sign_pdf_with_provenance(blank_pdf(), options).pdf_bytes


def build_sealed_package(base: Path, *, reading_copy: bytes | None = None) -> Path:
    """Build, seal and timestamp a small package below ``base`` and return its directory."""
    build_dir = base / "package"
    build_dir.mkdir()
    builder = PackageBuilder(build_dir)
    builder.add_bytes("capture.json", b"{}", role=FileRole.CAPTURE_DESCRIPTION, media_type="x/y")
    builder.add_bytes(
        "source/ticket.json", b'{"id": 1}', role=FileRole.SOURCE_TICKET, media_type="x/y"
    )
    if reading_copy is not None:
        builder.add_bytes(
            "reading-copy.pdf",
            reading_copy,
            role=FileRole.READING_COPY_ARCHIVAL,
            media_type="application/pdf",
        )
    builder.add_evidence("evidence/validation/01-cert.der", b"der")
    manifest_bytes = builder.write_manifest(
        revision_id="rev-1",
        revision_number=1,
        previous_revision_id=None,
        ticket=TicketRef(1, "10001"),
        captured_at=datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC),
        record_class="pilot-test",
        policy_id="sha256:" + "1" * 64,
        source_digest="sha256:" + "2" * 64,
        coverage=Coverage(
            CoverageCount(1, 1), CoverageCount(0, 0), OriginalEmailCoverage(0, 0), True
        ),
        producer=Producer("chronikwerk", "0"),
    )
    seal = asyncio.run(seal_manifest(manifest_bytes, seal_options(base)))
    token = asyncio.run(timestamp_seal(seal, timestamper()))
    (build_dir / SEAL_NAME).write_bytes(seal)
    (build_dir / TOKEN_NAME).write_bytes(token)
    return build_dir
