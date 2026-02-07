from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from zammad_pdf_archiver.adapters.signing.sign_pdf import sign_pdf
from zammad_pdf_archiver.domain.errors import PermanentError


def _minimal_pdf_bytes() -> bytes:
    # Minimal valid PDF with a single blank page.
    # Offsets are calculated to build a correct xref table.
    parts: list[bytes] = []
    parts.append(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")

    offsets: list[int] = [0]

    def add_obj(obj_num: int, body: bytes) -> None:
        offsets.append(sum(len(p) for p in parts))
        parts.append(f"{obj_num} 0 obj\n".encode("ascii"))
        parts.append(body)
        parts.append(b"\nendobj\n")

    add_obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    add_obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add_obj(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Contents 4 0 R >>",
    )
    add_obj(4, b"<< /Length 0 >>\nstream\n\nendstream")

    xref_start = sum(len(p) for p in parts)
    parts.append(b"xref\n")
    parts.append(b"0 5\n")
    parts.append(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        parts.append(f"{off:010d} 00000 n \n".encode("ascii"))

    parts.append(b"trailer\n")
    parts.append(b"<< /Size 5 /Root 1 0 R >>\n")
    parts.append(b"startxref\n")
    parts.append(f"{xref_start}\n".encode("ascii"))
    parts.append(b"%%EOF\n")
    return b"".join(parts)


def _write_test_pfx(path: Path, password: str) -> None:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Signer")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key=key, algorithm=hashes.SHA256())
    )

    pfx = pkcs12.serialize_key_and_certificates(
        name=b"test-signer",
        key=key,
        cert=cert,
        cas=None,
        encryption_algorithm=serialization.BestAvailableEncryption(password.encode("utf-8")),
    )
    path.write_bytes(pfx)


@dataclass(frozen=True)
class _DummyPades:
    reason: str = "Unit test"
    location: str = "CI"


@dataclass(frozen=True)
class _DummySigning:
    pfx_path: Path | None
    pfx_password: str | None
    pades: _DummyPades = _DummyPades()


@dataclass(frozen=True)
class _DummySettings:
    signing: _DummySigning


def test_sign_pdf_returns_pdf_bytes(tmp_path: Path) -> None:
    pfx_path = tmp_path / "test.pfx"
    _write_test_pfx(pfx_path, password="secret")

    settings = _DummySettings(
        signing=_DummySigning(pfx_path=pfx_path, pfx_password="secret"),
    )
    signed = sign_pdf(_minimal_pdf_bytes(), settings)
    assert signed.startswith(b"%PDF-")


def test_sign_pdf_missing_pfx_path_raises() -> None:
    settings = _DummySettings(signing=_DummySigning(pfx_path=None, pfx_password=None))
    with pytest.raises(PermanentError, match="pfx_path"):
        sign_pdf(_minimal_pdf_bytes(), settings)
