from __future__ import annotations

import hashlib
import sys
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from zammad_pdf_archiver.domain.time_utils import format_timestamp_utc


def compute_sha256(data: bytes) -> str:
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("data must be bytes")
    return hashlib.sha256(data).hexdigest()


def _safe_get_service_version(dist_name: str) -> str | None:
    try:
        return metadata.version(dist_name)
    except Exception:
        return None


def _extract_cert_fingerprint(settings: Any) -> str | None:
    """
    Best-effort extraction of a signing certificate fingerprint (SHA-256 hex).
    """
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.serialization import pkcs12

        pfx_path = getattr(settings, "pfx_path", None)
        if pfx_path is not None:
            password_secret = getattr(settings, "pfx_password", None)
            if isinstance(password_secret, SecretStr):
                password_str: str | None = password_secret.get_secret_value()
            elif isinstance(password_secret, str):
                password_str = password_secret
            elif password_secret is None:
                password_str = None
            else:
                password_str = str(password_secret)
            password = password_str.encode("utf-8") if password_str else None

            pfx_bytes = Path(pfx_path).read_bytes()
            _key, cert, _extra = pkcs12.load_key_and_certificates(pfx_bytes, password)
            if cert is None:
                return None
            return cert.fingerprint(hashes.SHA256()).hex()

        pades = getattr(settings, "pades", None)
        cert_path = getattr(pades, "cert_path", None) if pades is not None else None
        if cert_path is not None:
            raw = Path(cert_path).read_bytes()
            if raw.lstrip().startswith(b"-----BEGIN"):
                cert = x509.load_pem_x509_certificate(raw)
            else:
                cert = x509.load_der_x509_certificate(raw)
            return cert.fingerprint(hashes.SHA256()).hex()
    except Exception:
        return None
    return None


def _get_fingerprint(signing_settings: Any) -> str | None:
    if not signing_settings or not getattr(signing_settings, "enabled", False):
        return None
    return _extract_cert_fingerprint(signing_settings)


def build_audit_record(
    *,
    ticket_id: int,
    ticket_number: str,
    title: str | None,
    created_at: datetime,
    storage_path: str,
    sha256: str,
    signing_settings: Any | None = None,
    service_name: str = "zammad-pdf-archiver",
    service_dist_name: str = "zammad-pdf-archiver",
    attachments: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    signing_enabled = (
        bool(getattr(signing_settings, "enabled", False)) if signing_settings else False
    )
    timestamp = getattr(signing_settings, "timestamp", None) if signing_settings else None
    tsa_used = bool(getattr(timestamp, "enabled", False)) if timestamp is not None else False
    cert_fingerprint = (
        _get_fingerprint(signing_settings) if signing_settings else None
    )

    signing: dict[str, Any] = {"enabled": signing_enabled, "tsa_used": tsa_used}
    if cert_fingerprint:
        signing["cert_fingerprint"] = cert_fingerprint

    version = _safe_get_service_version(service_dist_name)
    service: dict[str, Any] = {
        "name": service_name,
        "version": version or "unknown",
        "python": sys.version.split(" ", 1)[0],
    }

    out: dict[str, Any] = {
        "ticket_id": int(ticket_id),
        "ticket_number": str(ticket_number),
        "title": (title or "").strip(),
        "created_at": format_timestamp_utc(created_at),
        "storage_path": str(storage_path),
        "sha256": str(sha256),
        "signing": signing,
        "service": service,
    }
    if attachments:
        out["attachments"] = attachments
    return out
