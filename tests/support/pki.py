"""Generate a small synthetic PKI for seal and timestamp tests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path

from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

PFX_PASSWORD = "synthetic-seal-password"


@dataclass(frozen=True, slots=True)
class Identity:
    """Hold one synthetic certificate and its private key."""

    certificate: x509.Certificate
    key: rsa.RSAPrivateKey


@dataclass(frozen=True, slots=True)
class SyntheticPki:
    """Hold the synthetic roots and leaves shared by seal tests."""

    root: Identity
    seal: Identity
    tsa: Identity
    expired_seal: Identity
    unrelated_root: Identity
    unrelated_tsa: Identity


_KEY_USAGE_FLAGS = (
    "digital_signature",
    "content_commitment",
    "key_encipherment",
    "data_encipherment",
    "key_agreement",
    "key_cert_sign",
    "crl_sign",
)


def _key_usage(*enabled: str) -> x509.KeyUsage:
    """Return a KeyUsage extension with only the named flags set."""
    flags = {flag: flag in enabled for flag in _KEY_USAGE_FLAGS}
    return x509.KeyUsage(**flags, encipher_only=False, decipher_only=False)


def _name(common_name: str) -> x509.Name:
    """Return a synthetic distinguished name for the given common name."""
    return x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Chronikwerk Synthetic Test"),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )


def _root(common_name: str, serial: int, now: datetime) -> Identity:
    """Create a self-signed root CA identity."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = _name(common_name)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(serial)
        .not_valid_before(now - timedelta(days=30))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(_key_usage("key_cert_sign", "crl_sign"), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(key, hashes.SHA256())
    )
    return Identity(certificate=certificate, key=key)


def _leaf(
    issuer: Identity,
    common_name: str,
    serial: int,
    validity: tuple[datetime, datetime],
    extensions: Sequence[tuple[x509.ExtensionType, bool]],
) -> Identity:
    """Create a leaf identity issued by the given CA with the extra extensions."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(common_name))
        .issuer_name(issuer.certificate.subject)
        .public_key(key.public_key())
        .serial_number(serial)
        .not_valid_before(validity[0])
        .not_valid_after(validity[1])
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer.key.public_key()),
            critical=False,
        )
    )
    for extension, critical in extensions:
        builder = builder.add_extension(extension, critical=critical)
    return Identity(certificate=builder.sign(issuer.key, hashes.SHA256()), key=key)


@cache
def build_pki() -> SyntheticPki:
    """Build (once per process) a root CA, seal and TSA leaves, an expired leaf and a stranger."""
    now = datetime.now(UTC).replace(microsecond=0)
    current = (now - timedelta(days=1), now + timedelta(days=365))
    seal_usage = [(_key_usage("digital_signature", "content_commitment"), True)]
    tsa_usage = [
        (_key_usage("digital_signature"), True),
        (x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), True),
    ]
    root = _root("Synthetic Seal Root CA", 1001, now)
    unrelated_root = _root("Synthetic Unrelated Root CA", 1002, now)
    return SyntheticPki(
        root=root,
        seal=_leaf(root, "Synthetic Manifest Seal", 2001, current, seal_usage),
        tsa=_leaf(root, "Synthetic Time Stamping Authority", 2002, current, tsa_usage),
        expired_seal=_leaf(
            root,
            "Synthetic Expired Seal",
            2003,
            (now - timedelta(days=60), now - timedelta(days=30)),
            seal_usage,
        ),
        unrelated_root=unrelated_root,
        unrelated_tsa=_leaf(
            unrelated_root, "Synthetic Unrelated Time Stamping Authority", 2004, current, tsa_usage
        ),
    )


def write_pkcs12(
    directory: Path,
    identity: Identity,
    chain: tuple[x509.Certificate, ...],
    *,
    name: str = "seal.pfx",
    password: str = PFX_PASSWORD,
) -> Path:
    """Write the identity and its chain as a password-protected PKCS#12 file."""
    path = directory / name
    path.write_bytes(
        pkcs12.serialize_key_and_certificates(
            b"synthetic-seal",
            identity.key,
            identity.certificate,
            list(chain),
            serialization.BestAvailableEncryption(password.encode("utf-8")),
        )
    )
    return path


def pem_bundle(*certificates: x509.Certificate) -> bytes:
    """Return the certificates concatenated as one PEM bundle."""
    return b"".join(cert.public_bytes(serialization.Encoding.PEM) for cert in certificates)


def asn1_certificate(certificate: x509.Certificate) -> asn1_x509.Certificate:
    """Convert a cryptography certificate into the asn1crypto form pyHanko expects."""
    return asn1_x509.Certificate.load(certificate.public_bytes(serialization.Encoding.DER))


def asn1_private_key(key: rsa.RSAPrivateKey) -> asn1_keys.PrivateKeyInfo:
    """Convert a cryptography private key into an asn1crypto PrivateKeyInfo."""
    return asn1_keys.PrivateKeyInfo.load(
        key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
