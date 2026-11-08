"""Verify sealed packages offline and prove that every kind of tampering is detected."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from pyhanko.sign import signers
from pyhanko.sign.timestamps.dummy_client import DummyTimeStamper
from pyhanko_certvalidator.registry import SimpleCertificateStore

from chronikwerk.preservation.manifest import MANIFEST_NAME, SEAL_NAME, TOKEN_NAME
from chronikwerk.preservation.seal import seal_manifest, timestamp_seal
from chronikwerk.preservation.verify import VerificationReport, verify_package_directory
from tests.support.pki import asn1_certificate, asn1_private_key, build_pki
from tests.support.sealed_package import (
    blank_pdf,
    build_sealed_package,
    load_anchors,
    seal_options,
    signed_pdf,
    timestamper,
)


def _verify(directory: Path, base: Path, *, trusted: bool = True) -> VerificationReport:
    """Verify a package directory against the synthetic (or an unrelated) root."""
    return asyncio.run(
        verify_package_directory(directory, trust_anchors=load_anchors(base, trusted=trusted))
    )


def _failed(report: VerificationReport) -> set[str]:
    """Return the names of the failed checks."""
    return {check.name for check in report.checks if not check.ok}


def _detail(report: VerificationReport, name: str) -> str:
    """Return the detail text of the named check."""
    return next(check.detail for check in report.checks if check.name == name)


def _copy(package: Path, base: Path) -> Path:
    """Return a fresh copy of ``package`` to tamper with."""
    target = base / "tampered"
    shutil.copytree(package, target)
    return target


def test_intact_package_passes(tmp_path: Path) -> None:
    report = _verify(build_sealed_package(tmp_path), tmp_path)

    assert report.passed
    assert report.revision_id == "rev-1"
    assert [check.name for check in report.checks] == [
        "manifest",
        "files",
        "seal",
        "timestamp",
        "pdf_signature",
    ]
    assert all(check.ok for check in report.checks)
    assert _detail(report, "pdf_signature") == "not signed (reading-copy.pdf absent)"
    assert "revocation" in _detail(report, "seal")


def test_details_say_revocation_was_not_checked(tmp_path: Path) -> None:
    report = _verify(build_sealed_package(tmp_path), tmp_path)

    assert "revocation not checked" in _detail(report, "seal")
    assert "revocation not checked" in _detail(report, "timestamp")


def test_expected_signer_pin_is_passed_through(tmp_path: Path) -> None:
    package = build_sealed_package(tmp_path)
    anchors = load_anchors(tmp_path)
    fingerprint = build_pki().seal.certificate.fingerprint(hashes.SHA256()).hex()

    pinned = asyncio.run(
        verify_package_directory(
            package, trust_anchors=anchors, expected_signer_sha256=[fingerprint]
        )
    )
    mismatched = asyncio.run(
        verify_package_directory(package, trust_anchors=anchors, expected_signer_sha256=["0" * 64])
    )

    assert pinned.passed
    assert _failed(mismatched) == {"seal"}
    assert "is not an expected signer" in _detail(mismatched, "seal")


def test_report_json_has_stable_keys(tmp_path: Path) -> None:
    report = _verify(build_sealed_package(tmp_path), tmp_path)

    document = json.loads(report.to_json())

    assert list(document) == ["revision_id", "result", "checks", "verified_at", "tool"]
    assert document["result"] == "passed"
    assert set(document["checks"][0]) == {"name", "ok", "detail"}
    assert set(document["tool"]) == {"name", "version"}


def test_changed_listed_file_fails_files(tmp_path: Path) -> None:
    package = _copy(build_sealed_package(tmp_path), tmp_path)
    (package / "source/ticket.json").write_bytes(b'{"id": 2}')

    report = _verify(package, tmp_path)

    assert _failed(report) == {"files"}
    assert "source/ticket.json (sha256)" in _detail(report, "files")


def test_deleted_listed_file_fails_files(tmp_path: Path) -> None:
    package = _copy(build_sealed_package(tmp_path), tmp_path)
    (package / "capture.json").unlink()

    report = _verify(package, tmp_path)

    assert _failed(report) == {"files"}
    assert "capture.json (missing)" in _detail(report, "files")


def test_unlisted_file_fails_files(tmp_path: Path) -> None:
    package = _copy(build_sealed_package(tmp_path), tmp_path)
    (package / "extra.txt").write_bytes(b"smuggled")

    report = _verify(package, tmp_path)

    assert _failed(report) == {"files"}
    assert "extra.txt (unlisted)" in _detail(report, "files")


def test_manifest_changed_by_one_byte_fails_seal(tmp_path: Path) -> None:
    package = _copy(build_sealed_package(tmp_path), tmp_path)
    manifest = (package / MANIFEST_NAME).read_bytes()
    (package / MANIFEST_NAME).write_bytes(manifest.replace(b"pilot-test", b"pilot-tesu"))

    report = _verify(package, tmp_path)

    assert _failed(report) == {"seal"}


def test_missing_manifest_skips_dependent_checks(tmp_path: Path) -> None:
    package = _copy(build_sealed_package(tmp_path), tmp_path)
    (package / MANIFEST_NAME).unlink()

    report = _verify(package, tmp_path)

    assert report.revision_id is None
    assert _failed(report) == {"manifest", "files", "seal"}
    assert _detail(report, "files").startswith("skipped:")


def test_seal_over_different_bytes_fails(tmp_path: Path) -> None:
    package = _copy(build_sealed_package(tmp_path), tmp_path)
    other = asyncio.run(seal_manifest(b"other manifest", seal_options(tmp_path)))
    (package / SEAL_NAME).write_bytes(other)

    report = _verify(package, tmp_path)

    assert "seal" in _failed(report)
    assert "does not match the manifest" in _detail(report, "seal")


def test_token_over_different_bytes_fails_timestamp(tmp_path: Path) -> None:
    package = _copy(build_sealed_package(tmp_path), tmp_path)
    token = asyncio.run(timestamp_seal(b"some other seal", timestamper()))
    (package / TOKEN_NAME).write_bytes(token)

    report = _verify(package, tmp_path)

    assert _failed(report) == {"timestamp"}
    assert "does not cover the seal" in _detail(report, "timestamp")


def test_missing_seal_and_token_fail(tmp_path: Path) -> None:
    package = _copy(build_sealed_package(tmp_path), tmp_path)
    (package / SEAL_NAME).unlink()
    (package / TOKEN_NAME).unlink()

    report = _verify(package, tmp_path)

    assert _failed(report) == {"seal", "timestamp"}


def test_untrusted_anchors_fail_seal_and_timestamp(tmp_path: Path) -> None:
    report = _verify(build_sealed_package(tmp_path), tmp_path, trusted=False)

    assert _failed(report) == {"seal", "timestamp"}
    assert "does not chain to a trust anchor" in _detail(report, "seal")


def test_signed_reading_copy_passes(tmp_path: Path) -> None:
    package = build_sealed_package(tmp_path, reading_copy=signed_pdf(tmp_path))

    report = _verify(package, tmp_path)

    assert report.passed, report.checks
    assert _detail(report, "pdf_signature").startswith("signature 1 valid")


def test_signed_reading_copy_with_untrusted_anchors_fails_pdf_signature(tmp_path: Path) -> None:
    package = build_sealed_package(tmp_path, reading_copy=signed_pdf(tmp_path))

    report = _verify(package, tmp_path, trusted=False)

    assert "pdf_signature" in _failed(report)


def test_unsigned_reading_copy_is_reported_as_not_signed(tmp_path: Path) -> None:
    package = build_sealed_package(tmp_path, reading_copy=blank_pdf())

    report = _verify(package, tmp_path)

    assert report.passed
    assert _detail(report, "pdf_signature") == "not signed"


def test_altered_signed_reading_copy_fails_files_and_pdf_signature(tmp_path: Path) -> None:
    package = build_sealed_package(tmp_path, reading_copy=signed_pdf(tmp_path))
    pdf = package / "reading-copy.pdf"
    data = bytearray(pdf.read_bytes())
    data[data.index(b"/MediaBox") + 1] ^= 0x01
    pdf.write_bytes(bytes(data))

    report = _verify(package, tmp_path)

    assert {"files", "pdf_signature"} <= _failed(report)


def _reseal_with_expired_signer(package: Path) -> datetime:
    """Seal the manifest with the expired identity, timestamped inside its validity window."""
    pki = build_pki()
    signer = signers.SimpleSigner(
        signing_cert=asn1_certificate(pki.expired_seal.certificate),
        signing_key=asn1_private_key(pki.expired_seal.key),
        cert_registry=SimpleCertificateStore.from_certs([asn1_certificate(pki.root.certificate)]),
    )
    manifest = (package / MANIFEST_NAME).read_bytes()
    seal = asyncio.run(
        signer.async_sign_general_data(manifest, "sha256", detached=True, use_cades=True)
    ).dump()
    sealed_at = pki.expired_seal.certificate.not_valid_after_utc - timedelta(days=1)
    stamper = DummyTimeStamper(
        tsa_cert=asn1_certificate(pki.tsa.certificate),
        tsa_key=asn1_private_key(pki.tsa.key),
        certs_to_embed=SimpleCertificateStore.from_certs([asn1_certificate(pki.root.certificate)]),
        fixed_dt=sealed_at,
    )
    (package / SEAL_NAME).write_bytes(seal)
    (package / TOKEN_NAME).write_bytes(asyncio.run(timestamp_seal(seal, stamper)))
    return sealed_at


def test_seal_expired_since_is_checked_as_of_its_trusted_timestamp(tmp_path: Path) -> None:
    package = _copy(build_sealed_package(tmp_path), tmp_path)
    _reseal_with_expired_signer(package)

    report = _verify(package, tmp_path)

    assert report.passed, report.checks


def test_seal_expired_without_a_valid_timestamp_is_checked_as_of_now(tmp_path: Path) -> None:
    package = _copy(build_sealed_package(tmp_path), tmp_path)
    _reseal_with_expired_signer(package)
    (package / TOKEN_NAME).write_bytes(asyncio.run(timestamp_seal(b"other", timestamper())))

    report = _verify(package, tmp_path)

    assert _failed(report) == {"seal", "timestamp"}
