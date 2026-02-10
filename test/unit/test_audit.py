from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from zammad_pdf_archiver.domain.audit import build_audit_record, compute_sha256


def test_compute_sha256_matches_hashlib() -> None:
    expected = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert compute_sha256(b"abc") == expected


def test_compute_sha256_rejects_non_bytes() -> None:
    with pytest.raises(TypeError, match="data must be bytes"):
        compute_sha256("abc")  # type: ignore[arg-type]


def test_build_audit_record_normalizes_timestamp_and_title() -> None:
    created_at = datetime(2026, 2, 7, 12, 0, 0, 987654, tzinfo=UTC)
    audit = build_audit_record(
        ticket_id=123,
        ticket_number="T-123",
        title="  Hello  ",
        created_at=created_at,
        storage_path="/mnt/archive/T-123.pdf",
        sha256="deadbeef",
        signing_settings=None,
        service_dist_name="definitely-not-an-installed-dist-name",
    )

    assert audit["created_at"] == "2026-02-07T12:00:00Z"
    assert audit["title"] == "Hello"
    assert audit["service"]["version"] == "unknown"
    assert audit["signing"] == {"enabled": False, "tsa_used": False}


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


def _cert_fingerprint_from_pfx(path: Path, password: str) -> str:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.serialization import pkcs12

    pfx_bytes = path.read_bytes()
    _key, cert, _extra = pkcs12.load_key_and_certificates(pfx_bytes, password.encode("utf-8"))
    assert cert is not None
    return cert.fingerprint(hashes.SHA256()).hex()


@dataclass(frozen=True)
class _DummySigning:
    enabled: bool
    pfx_path: Path | None
    pfx_password: SecretStr | None


def test_build_audit_record_extracts_cert_fingerprint_from_pfx(tmp_path: Path) -> None:
    pfx_path = tmp_path / "test.pfx"
    _write_test_pfx(pfx_path, password="secret")

    signing = _DummySigning(enabled=True, pfx_path=pfx_path, pfx_password=SecretStr("secret"))
    audit = build_audit_record(
        ticket_id=1,
        ticket_number="T1",
        title=None,
        created_at=datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC),
        storage_path="/mnt/archive/T1.pdf",
        sha256="00",
        signing_settings=signing,
        service_dist_name="definitely-not-an-installed-dist-name",
    )

    expected = _cert_fingerprint_from_pfx(pfx_path, password="secret")
    assert audit["signing"]["enabled"] is True
    assert audit["signing"]["cert_fingerprint"] == expected


def test_build_audit_record_includes_attachments_when_provided() -> None:
    """Optional attachment list is added to audit record (PRD §8.2)."""
    audit = build_audit_record(
        ticket_id=1,
        ticket_number="T1",
        title="t",
        created_at=datetime(2026, 2, 7, 12, 0, 0, tzinfo=UTC),
        storage_path="/mnt/archive/T1.pdf",
        sha256="ab",
        attachments=[
            {
                "storage_path": "/mnt/archive/attachments/1_10_file.txt",
                "article_id": 1,
                "attachment_id": 10,
                "filename": "file.txt",
                "sha256": "cd",
            },
        ],
    )
    assert audit["attachments"] == [
        {
            "storage_path": "/mnt/archive/attachments/1_10_file.txt",
            "article_id": 1,
            "attachment_id": 10,
            "filename": "file.txt",
            "sha256": "cd",
        },
    ]
