"""Apply PAdES signatures to archived PDFs when signing is enabled."""

# pylint: disable=import-outside-toplevel
from __future__ import annotations

import io
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx

from chronikwerk.documents.options import SigningOptions
from chronikwerk.failures import PermanentError, TransientError

# Interval (seconds) between full PFX certificate re-parses for cached signers.
_CERT_CHECK_INTERVAL_SECONDS = 3600


@dataclass(frozen=True)
class _PfxMaterial:
    path: Path
    pfx_bytes: bytes
    password: bytes | None


def _missing_signing_dependency(exc: ImportError) -> PermanentError:
    return PermanentError(
        f"Signing dependencies are not installed ({exc.name}). "
        "Install chronikwerk[signing] or disable signing.enabled."
    )


def _load_pfx(signing: SigningOptions) -> _PfxMaterial:
    pfx_path = signing.pfx_path
    if pfx_path is None:
        raise PermanentError("Missing signing material: signing.pfx_path")

    path = Path(pfx_path)
    if not path.exists() or not path.is_file():
        raise PermanentError(f"PFX file not found: {path}")

    password = signing.pfx_password.encode("utf-8") if signing.pfx_password else None
    return _PfxMaterial(path=path, pfx_bytes=path.read_bytes(), password=password)


def _validate_certificate_validity(not_before: datetime, not_after: datetime) -> None:
    now = datetime.now(UTC)
    if now < not_before:
        raise PermanentError(f"Signing certificate is not valid before {not_before.isoformat()}")
    if now >= not_after:
        raise PermanentError(f"Signing certificate expired on {not_after.isoformat()}")


def _validate_cert_not_expired(
    pfx_bytes: bytes, password: bytes | None
) -> tuple[datetime, datetime]:
    # Import lazily to keep non-signing code paths importable without crypto deps.
    try:
        from cryptography.hazmat.primitives.serialization import pkcs12
    except ImportError as exc:
        raise _missing_signing_dependency(exc) from exc

    try:
        key, cert, _extra = pkcs12.load_key_and_certificates(pfx_bytes, password)
    except ValueError as exc:
        hint = "wrong password" if password else "missing/incorrect password"
        raise PermanentError(
            f"Failed to load PKCS#12/PFX bundle ({hint} or corrupted file)"
        ) from exc

    if key is None or cert is None:
        raise PermanentError("PKCS#12/PFX bundle must contain a private key and certificate")

    not_before = cert.not_valid_before_utc
    not_after = cert.not_valid_after_utc
    _validate_certificate_validity(not_before, not_after)
    return not_before, not_after


@dataclass
class _CachedSigner:
    signer: Any  # signers.SimpleSigner
    pfx_bytes: bytes
    password: bytes | None
    certificate_fingerprint: str
    certificate_not_before: datetime
    certificate_not_after: datetime
    last_cert_check: float


_signer_cache_lock = threading.Lock()
_signer_cache: dict[str, _CachedSigner] = {}


def _cached_signer_for_material(
    pfx_path_str: str,
    pfx: _PfxMaterial,
    now: float,
) -> tuple[_CachedSigner | None, tuple[bytes, bytes | None] | None]:
    with _signer_cache_lock:
        cached = _signer_cache.get(pfx_path_str)
        if cached is None or cached.pfx_bytes != pfx.pfx_bytes or cached.password != pfx.password:
            return None, None
        _validate_certificate_validity(
            cached.certificate_not_before,
            cached.certificate_not_after,
        )
        if now - cached.last_cert_check < _CERT_CHECK_INTERVAL_SECONDS:
            return cached, None
        return None, (cached.pfx_bytes, cached.password)


def _signer_after_cert_recheck(
    pfx_path_str: str,
    pfx: _PfxMaterial,
    check_material: tuple[bytes, bytes | None] | None,
) -> _CachedSigner | None:
    if check_material is None:
        return None

    pfx_bytes_for_check, password_for_check = check_material
    _validate_cert_not_expired(pfx_bytes_for_check, password_for_check)
    with _signer_cache_lock:
        cached = _signer_cache.get(pfx_path_str)
        if cached is None or cached.pfx_bytes != pfx.pfx_bytes or cached.password != pfx.password:
            return None
        _validate_certificate_validity(
            cached.certificate_not_before,
            cached.certificate_not_after,
        )
        cached.last_cert_check = time.monotonic()
        return cached


def _build_signer_entry(
    pfx: _PfxMaterial,
) -> _CachedSigner:
    try:
        from pyhanko.sign import signers
    except ImportError as exc:
        raise _missing_signing_dependency(exc) from exc

    not_before, not_after = _validate_cert_not_expired(pfx.pfx_bytes, pfx.password)
    try:
        signer = signers.SimpleSigner.load_pkcs12_data(
            pfx.pfx_bytes,
            other_certs=(),
            passphrase=pfx.password,
        )
    except ValueError as exc:
        raise PermanentError("Failed to initialise signer from PKCS#12/PFX bundle") from exc
    if signer is None:
        raise PermanentError("Failed to initialise signer from PKCS#12/PFX bundle")

    return _CachedSigner(
        signer=signer,
        pfx_bytes=pfx.pfx_bytes,
        password=pfx.password,
        certificate_fingerprint=sha256(signer.signing_cert.dump()).hexdigest(),
        certificate_not_before=not_before,
        certificate_not_after=not_after,
        last_cert_check=time.monotonic(),
    )


def _cache_new_signer(
    pfx_path_str: str,
    entry: _CachedSigner,
) -> _CachedSigner:
    with _signer_cache_lock:
        existing = _signer_cache.get(pfx_path_str)
        if (
            existing is not None
            and existing.pfx_bytes == entry.pfx_bytes
            and existing.password == entry.password
        ):
            _validate_certificate_validity(
                existing.certificate_not_before,
                existing.certificate_not_after,
            )
            return existing
        _validate_certificate_validity(
            entry.certificate_not_before,
            entry.certificate_not_after,
        )
        _signer_cache[pfx_path_str] = entry
        return entry


def _get_cached_signer(pfx: _PfxMaterial) -> _CachedSigner:
    """Return a cached signer for the loaded PFX bytes and password.

    Stored certificate validity bounds are checked on every cache hit. The PFX is
    re-validated at most once per hour to detect malformed material or stale cache state.
    """
    pfx_path_str = str(pfx.path)
    now = time.monotonic()

    entry, check_material = _cached_signer_for_material(pfx_path_str, pfx, now)
    if entry is not None:
        return entry

    entry = _signer_after_cert_recheck(pfx_path_str, pfx, check_material)
    if entry is not None:
        return entry

    return _cache_new_signer(pfx_path_str, _build_signer_entry(pfx))


def _classify_signing_failure(exc: Exception) -> PermanentError | TransientError:
    if isinstance(
        exc,
        httpx.TimeoutException | httpx.ConnectError | ConnectionError | OSError | TimeoutError,
    ):
        return TransientError("Failed to sign PDF due to temporary (TSA) network issue")
    return PermanentError("Failed to sign PDF")


@dataclass(frozen=True)
class SignedPdf:
    """Carry signed bytes and signature metadata through the pipeline."""

    pdf_bytes: bytes
    certificate_fingerprint: str


@dataclass(frozen=True)
class _PdfSigningSession:
    pdf_signer: Any
    writer_type: Any
    certificate_fingerprint: str


def _optional_timestamper(signing: SigningOptions) -> Any | None:
    if not signing.timestamp.enabled:
        return None
    try:
        from chronikwerk.documents.tsa import build_timestamper
    except ImportError as exc:
        raise _missing_signing_dependency(exc) from exc
    return build_timestamper(signing.timestamp)


def _apply_pdf_signature(pdf_bytes: bytes, *, pdf_signer: Any, writer_type: Any) -> bytes:
    out = io.BytesIO()
    try:
        writer = writer_type(io.BytesIO(bytes(pdf_bytes)))
        pdf_signer.sign_pdf(writer, output=out)
    except TransientError, PermanentError:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _classify_signing_failure(exc) from exc
    return out.getvalue()


def _build_pdf_signing_session(
    pfx: _PfxMaterial,
    signing: SigningOptions,
) -> _PdfSigningSession:
    try:
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.sign.fields import SigFieldSpec, SigSeedSubFilter
        from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata, PdfSigner
    except ImportError as exc:
        raise _missing_signing_dependency(exc) from exc

    signer_entry = _get_cached_signer(pfx)
    field_name = "Signature1"
    meta = PdfSignatureMetadata(
        field_name=field_name,
        reason=signing.reason,
        location=signing.location,
        subfilter=SigSeedSubFilter.PADES,
    )
    pdf_signer = PdfSigner(
        signature_meta=meta,
        signer=signer_entry.signer,
        timestamper=_optional_timestamper(signing),
        new_field_spec=SigFieldSpec(
            sig_field_name=field_name,
            box=(0, 0, 0, 0),
        ),
    )
    return _PdfSigningSession(
        pdf_signer=pdf_signer,
        writer_type=IncrementalPdfFileWriter,
        certificate_fingerprint=signer_entry.certificate_fingerprint,
    )


def sign_pdf_with_provenance(
    pdf_bytes: bytes,
    signing: SigningOptions,
) -> SignedPdf:
    """
    Sign a PDF with an (invisible) PAdES signature using a locally provided PKCS#12/PFX bundle.

    If enabled via settings, an RFC3161 TSA timestamp will be embedded (PAdES-T style).
    """
    if not isinstance(pdf_bytes, bytes | bytearray) or not pdf_bytes:
        raise ValueError("pdf_bytes must be non-empty bytes")

    pfx = _load_pfx(signing)
    session = _build_pdf_signing_session(
        pfx,
        signing,
    )
    return SignedPdf(
        pdf_bytes=_apply_pdf_signature(
            pdf_bytes,
            pdf_signer=session.pdf_signer,
            writer_type=session.writer_type,
        ),
        certificate_fingerprint=session.certificate_fingerprint,
    )


def sign_pdf(
    pdf_bytes: bytes,
    signing: SigningOptions,
) -> bytes:
    """Sign a PDF while preserving the public bytes-only adapter contract."""
    return sign_pdf_with_provenance(
        pdf_bytes,
        signing,
    ).pdf_bytes
