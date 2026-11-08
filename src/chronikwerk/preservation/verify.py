"""Verify a preservation package directory offline against trust anchors."""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from asn1crypto import x509 as asn1_x509
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.sign.validation import async_validate_pdf_signature
from pyhanko_certvalidator import ValidationContext

from chronikwerk._version import __version__
from chronikwerk.preservation.manifest import (
    MANIFEST_NAME,
    SEAL_NAME,
    TOKEN_NAME,
    Manifest,
    ManifestError,
    parse_manifest,
)
from chronikwerk.preservation.package import verify_package_files
from chronikwerk.preservation.seal import check_seal, check_timestamp

__all__ = [
    "READING_COPY_NAME",
    "VerificationCheck",
    "VerificationReport",
    "verify_package_directory",
]

READING_COPY_NAME = "reading-copy.pdf"
_TOOL_NAME = "chronikwerk-admin verify"
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_Anchors = tuple[asn1_x509.Certificate, ...] | list[asn1_x509.Certificate]


@dataclass(frozen=True, slots=True)
class VerificationCheck:
    """Outcome of one verification step."""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """All verification steps for one package and their overall result."""

    revision_id: str | None
    passed: bool
    checks: tuple[VerificationCheck, ...]
    verified_at: datetime
    tool_version: str

    def to_json(self) -> bytes:
        """Serialize the report with stable keys as UTF-8 JSON with a trailing newline."""
        document = {
            "revision_id": self.revision_id,
            "result": "passed" if self.passed else "failed",
            "checks": [
                {"name": check.name, "ok": check.ok, "detail": check.detail}
                for check in self.checks
            ],
            "verified_at": self.verified_at.astimezone(UTC).strftime(_TIME_FORMAT),
            "tool": {"name": _TOOL_NAME, "version": self.tool_version},
        }
        return (json.dumps(document, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _read_regular(directory: Path, name: str) -> bytes | None:
    """Return the bytes of a regular, non-symlink file, or ``None`` when it is absent."""
    path = directory / name
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _skipped(name: str, reason: str) -> VerificationCheck:
    return VerificationCheck(name, False, f"skipped: {reason}")


def _check_manifest(manifest_bytes: bytes | None) -> tuple[VerificationCheck, Manifest | None]:
    if manifest_bytes is None:
        return VerificationCheck("manifest", False, f"{MANIFEST_NAME} is missing"), None
    try:
        manifest = parse_manifest(manifest_bytes)
    except ManifestError as exc:
        return VerificationCheck("manifest", False, str(exc)), None
    detail = f"revision {manifest.revision_id}, {len(manifest.files)} listed files"
    return VerificationCheck("manifest", True, detail), manifest


def _check_files(directory: Path, manifest: Manifest | None) -> VerificationCheck:
    if manifest is None:
        return _skipped("files", "no valid manifest")
    problems = verify_package_files(directory, manifest)
    if not problems:
        return VerificationCheck("files", True, f"{len(manifest.files)} files match")
    listing = ", ".join(f"{problem.path} ({problem.kind})" for problem in problems)
    return VerificationCheck("files", False, listing)


async def _check_seal(
    manifest_bytes: bytes | None,
    seal_bytes: bytes | None,
    anchors: _Anchors,
    validation_time: datetime | None,
    expected_signer_sha256: Collection[str] | None,
) -> VerificationCheck:
    if manifest_bytes is None:
        return _skipped("seal", f"{MANIFEST_NAME} is missing")
    if seal_bytes is None:
        return VerificationCheck("seal", False, f"{SEAL_NAME} is missing")
    seal = await check_seal(
        manifest_bytes,
        seal_bytes,
        trust_anchors=anchors,
        validation_time=validation_time,
        expected_signer_sha256=expected_signer_sha256,
    )
    detail = "; ".join(seal.problems)
    if seal.ok and seal.signer_subject:
        detail = f"signed by {seal.signer_subject}; {detail}"
    return VerificationCheck("seal", seal.ok, detail)


async def _check_timestamp(
    seal_bytes: bytes | None,
    token_bytes: bytes | None,
    anchors: _Anchors,
    validation_time: datetime | None,
) -> tuple[VerificationCheck, datetime | None]:
    """Check the token; also return its time when it is trusted and covers the seal."""
    if seal_bytes is None:
        return _skipped("timestamp", f"{SEAL_NAME} is missing"), None
    if token_bytes is None:
        return VerificationCheck("timestamp", False, f"{TOKEN_NAME} is missing"), None
    token = await check_timestamp(
        seal_bytes, token_bytes, trust_anchors=anchors, validation_time=validation_time
    )
    detail = "; ".join(token.problems)
    proven_time = token.gen_time if token.ok else None
    if proven_time is not None:
        detail = f"timestamped {proven_time.astimezone(UTC).strftime(_TIME_FORMAT)}; {detail}"
    return VerificationCheck("timestamp", token.ok, detail), proven_time


async def _validate_embedded(reading_copy: Path, context: ValidationContext) -> list[Any]:
    with reading_copy.open("rb") as handle:
        reader = PdfFileReader(handle)
        signatures = list(reader.embedded_regular_signatures)
        return [
            await async_validate_pdf_signature(signature, signer_validation_context=context)
            for signature in signatures
        ]


def _describe_pdf_status(index: int, status: Any) -> tuple[bool, str]:
    ok = bool(status.bottom_line)
    state = "valid" if ok else "INVALID"
    return ok, f"signature {index} {state}: {status.summary()}"


async def _check_pdf_signature(
    directory: Path, anchors: _Anchors, validation_time: datetime | None
) -> VerificationCheck:
    reading_copy = directory / READING_COPY_NAME
    if reading_copy.is_symlink() or not reading_copy.is_file():
        return VerificationCheck("pdf_signature", True, f"not signed ({READING_COPY_NAME} absent)")
    context = ValidationContext(
        trust_roots=list(anchors),
        allow_fetching=False,
        revocation_mode="soft-fail",
        moment=validation_time,
    )
    try:
        statuses = await _validate_embedded(reading_copy, context)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        message = f"PDF signature could not be validated: {type(exc).__name__}: {exc}"
        return VerificationCheck("pdf_signature", False, message)
    if not statuses:
        return VerificationCheck("pdf_signature", True, "not signed")
    described = [_describe_pdf_status(index, s) for index, s in enumerate(statuses, start=1)]
    return VerificationCheck(
        "pdf_signature", all(ok for ok, _ in described), "; ".join(text for _, text in described)
    )


async def verify_package_directory(
    directory: Path,
    *,
    trust_anchors: _Anchors,
    validation_time: datetime | None = None,
    expected_signer_sha256: Collection[str] | None = None,
) -> VerificationReport:
    """Run every offline check on a package directory; never raises for a defective package.

    ``expected_signer_sha256`` pins the seal signer certificate by SHA-256 fingerprint.
    Without ``validation_time`` the seal is validated as of its trusted timestamp, and the
    timestamp and PDF signature as of now.
    """
    manifest_bytes = _read_regular(directory, MANIFEST_NAME)
    seal_bytes = _read_regular(directory, SEAL_NAME)
    token_bytes = _read_regular(directory, TOKEN_NAME)
    manifest_check, manifest = _check_manifest(manifest_bytes)
    timestamp_check, sealed_at = await _check_timestamp(
        seal_bytes, token_bytes, trust_anchors, validation_time
    )
    checks = (
        manifest_check,
        _check_files(directory, manifest),
        # A trusted token proves the seal existed at its time, so the seal signer's chain is
        # checked as of then: a seal certificate that expired later does not fail the package.
        await _check_seal(
            manifest_bytes,
            seal_bytes,
            trust_anchors,
            validation_time or sealed_at,
            expected_signer_sha256,
        ),
        timestamp_check,
        await _check_pdf_signature(directory, trust_anchors, validation_time),
    )
    return VerificationReport(
        revision_id=manifest.revision_id if manifest is not None else None,
        passed=all(check.ok for check in checks),
        checks=checks,
        verified_at=datetime.now(UTC),
        tool_version=__version__,
    )
