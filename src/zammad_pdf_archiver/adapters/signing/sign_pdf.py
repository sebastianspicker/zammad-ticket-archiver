from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zammad_pdf_archiver.domain.errors import PermanentError, TransientError


@dataclass(frozen=True)
class _PfxMaterial:
    path: Path
    pfx_bytes: bytes
    password: bytes | None


def _secret_to_str(value: Any) -> str | None:
    if value is None:
        return None
    get_secret = getattr(value, "get_secret_value", None)
    if callable(get_secret):
        return get_secret()
    if isinstance(value, str):
        return value
    return str(value)


def _load_pfx(settings: Any) -> _PfxMaterial:
    signing = getattr(settings, "signing", None)
    if signing is None:
        raise ValueError("settings must have a .signing attribute")

    pfx_path = getattr(signing, "pfx_path", None)
    if pfx_path is None:
        raise PermanentError("Missing signing material: settings.signing.pfx_path")

    path = Path(pfx_path)
    if not path.exists() or not path.is_file():
        raise PermanentError(f"PFX file not found: {path}")

    password_str = _secret_to_str(getattr(signing, "pfx_password", None))
    password = password_str.encode("utf-8") if password_str else None
    return _PfxMaterial(path=path, pfx_bytes=path.read_bytes(), password=password)


def _validate_cert_not_expired(pfx_bytes: bytes, password: bytes | None) -> None:
    # Import lazily to keep non-signing code paths importable without crypto deps.
    from cryptography.hazmat.primitives.serialization import pkcs12

    try:
        key, cert, _extra = pkcs12.load_key_and_certificates(pfx_bytes, password)
    except ValueError as exc:
        hint = "wrong password" if password else "missing/incorrect password"
        raise PermanentError(
            f"Failed to load PKCS#12/PFX bundle ({hint} or corrupted file)"
        ) from exc

    if key is None or cert is None:
        raise PermanentError("PKCS#12/PFX bundle must contain a private key and certificate")

    now = datetime.now(UTC)
    not_before = getattr(cert, "not_valid_before_utc", None) or cert.not_valid_before.replace(
        tzinfo=UTC
    )
    not_after = getattr(cert, "not_valid_after_utc", None) or cert.not_valid_after.replace(
        tzinfo=UTC
    )

    if now < not_before:
        raise PermanentError(
            f"Signing certificate is not valid before {not_before.isoformat()}"
        )
    if now > not_after:
        raise PermanentError(f"Signing certificate expired on {not_after.isoformat()}")


def sign_pdf(pdf_bytes: bytes, settings: Any) -> bytes:
    """
    Sign a PDF with an (invisible) PAdES signature using a locally provided PKCS#12/PFX bundle.

    If enabled via settings, an RFC3161 TSA timestamp will be embedded (PAdES-T style).
    """
    if not isinstance(pdf_bytes, (bytes, bytearray)) or not pdf_bytes:
        raise ValueError("pdf_bytes must be non-empty bytes")

    pfx = _load_pfx(settings)
    _validate_cert_not_expired(pfx.pfx_bytes, pfx.password)

    # Import lazily so the rest of the service stays importable even if pyHanko isn't installed.
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers
    from pyhanko.sign.fields import SigFieldSpec
    from pyhanko.sign.signers.pdf_signer import PdfSignatureMetadata, PdfSigner

    reason = None
    location = None
    signing = getattr(settings, "signing", None)
    pades = getattr(signing, "pades", None) if signing is not None else None
    if pades is not None:
        reason = getattr(pades, "reason", None)
        location = getattr(pades, "location", None)

    try:
        signer = signers.SimpleSigner.load_pkcs12(pfx.path, passphrase=pfx.password)
    except Exception as exc:  # noqa: BLE001 - surface as PermanentError with context
        raise PermanentError("Failed to initialise signer from PKCS#12/PFX bundle") from exc

    field_name = "Signature1"
    meta = PdfSignatureMetadata(field_name=field_name, reason=reason, location=location)

    timestamper = None
    timestamp_settings = getattr(signing, "timestamp", None) if signing is not None else None
    tsa_settings = getattr(signing, "tsa", None) if signing is not None else None
    tsa_enabled = bool(
        getattr(timestamp_settings, "enabled", False) or getattr(tsa_settings, "enabled", False)
    )
    if tsa_enabled:
        from zammad_pdf_archiver.adapters.signing.tsa_rfc3161 import build_timestamper

        timestamper = build_timestamper(settings)

    pdf_signer = PdfSigner(
        signature_meta=meta,
        signer=signer,
        timestamper=timestamper,
        new_field_spec=SigFieldSpec(
            sig_field_name=field_name,
            box=(0, 0, 0, 0),
        ),
    )

    out = io.BytesIO()
    try:
        writer = IncrementalPdfFileWriter(io.BytesIO(bytes(pdf_bytes)))
        pdf_signer.sign_pdf(writer, output=out)
    except (TransientError, PermanentError):
        raise
    except Exception as exc:  # noqa: BLE001 - map pyHanko errors to PermanentError for callers
        raise PermanentError("Failed to sign PDF") from exc
    return out.getvalue()
