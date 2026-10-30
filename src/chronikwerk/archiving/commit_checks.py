"""Pre-completion seal and timestamp checks, store error mapping and read-back verification."""

# Contract: preservation brief (ADRs 0011-0013) sections 5.5 and 5.6.

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from asn1crypto import x509 as asn1_x509
from cryptography.hazmat.primitives.serialization import Encoding, pkcs12

from chronikwerk.archiving.commit_errors import (
    SealCheckFailedError,
    StoreConflictError,
    StoreWriteFailedError,
    TimestampCheckFailedError,
    VerificationFailedError,
)
from chronikwerk.archiving.error_policy import describe_os_error
from chronikwerk.preservation.manifest import MANIFEST_NAME, SEAL_NAME, TOKEN_NAME, Manifest
from chronikwerk.preservation.seal import SealOptions, check_seal, check_timestamp
from chronikwerk.storage.archive_store import (
    ArchiveStoreError,
    ObjectExistsError,
    RevisionExistsError,
    RevisionWriter,
)

__all__ = [
    "GEN_TIME_TOLERANCE",
    "SEAL_FILES",
    "check_seal_and_token",
    "read_back",
    "read_object",
    "seal_signer_fingerprint",
    "store_errors",
]

GEN_TIME_TOLERANCE = timedelta(minutes=5)
SEAL_FILES = (MANIFEST_NAME, SEAL_NAME, TOKEN_NAME)
_CHUNK_BYTES = 1024 * 1024
_Anchors = tuple[asn1_x509.Certificate, ...] | list[asn1_x509.Certificate]


def seal_signer_fingerprint(options: SealOptions) -> str:
    """Return the SHA-256 fingerprint (lowercase hex) of the configured seal certificate."""
    password = options.pfx_password.encode("utf-8") if options.pfx_password else None
    try:
        _, certificate, _ = pkcs12.load_key_and_certificates(
            options.pfx_path.read_bytes(), password
        )
    except (OSError, ValueError) as exc:
        raise SealCheckFailedError(f"seal PFX cannot be read: {type(exc).__name__}") from exc
    if certificate is None:
        raise SealCheckFailedError("seal PFX holds no certificate")
    return hashlib.sha256(certificate.public_bytes(Encoding.DER)).hexdigest()


async def check_seal_and_token(
    manifest_bytes: bytes,
    seal_bytes: bytes,
    token_bytes: bytes,
    *,
    trust_anchors: _Anchors,
    expected_signer_sha256: str,
    window: tuple[datetime, datetime],
) -> None:
    """Raise unless the seal and token are valid, trusted and timestamped inside ``window``.

    ``window`` is ``(collection start, now)``; both ends get ``GEN_TIME_TOLERANCE``.
    """
    seal = await check_seal(
        manifest_bytes,
        seal_bytes,
        trust_anchors=trust_anchors,
        expected_signer_sha256={expected_signer_sha256},
    )
    if not seal.ok:
        raise SealCheckFailedError("seal check failed: " + "; ".join(seal.problems))
    token = await check_timestamp(seal_bytes, token_bytes, trust_anchors=trust_anchors)
    if not token.ok:
        raise TimestampCheckFailedError("timestamp check failed: " + "; ".join(token.problems))
    earliest, latest = window[0] - GEN_TIME_TOLERANCE, window[1] + GEN_TIME_TOLERANCE
    if token.gen_time is None or not earliest <= token.gen_time <= latest:
        raise TimestampCheckFailedError(
            f"timestamp genTime {token.gen_time} lies outside the collection window "
            f"{earliest.isoformat()} .. {latest.isoformat()}"
        )


@contextmanager
def store_errors(action: str) -> Iterator[None]:
    """Report store failures as commit errors: conflicts are permanent, the rest transient.

    An ``OSError`` is reduced to its errno summary: its text names host paths, which belong in
    the log (the chained cause), not in the ticket note or the journal.
    """
    try:
        yield
    except (ObjectExistsError, RevisionExistsError) as exc:
        raise StoreConflictError(f"{action}: {exc}") from exc
    except ArchiveStoreError as exc:
        raise StoreWriteFailedError(f"{action}: {type(exc).__name__}: {exc}") from exc
    except OSError as exc:
        reason = describe_os_error(exc)
        raise StoreWriteFailedError(f"{action}: {type(exc).__name__}: {reason}") from exc


def _digest(writer: RevisionWriter, key: str) -> tuple[int, str, bytes | None]:
    """Return ``(size, sha256 hex, bytes)``; bytes are kept only for the small seal files."""
    digest = hashlib.sha256()
    size = 0
    kept: list[bytes] | None = [] if key in SEAL_FILES else None
    try:
        with writer.open(key) as handle:
            while chunk := handle.read(_CHUNK_BYTES):
                digest.update(chunk)
                size += len(chunk)
                if kept is not None:
                    kept.append(chunk)
    except FileNotFoundError as exc:
        raise VerificationFailedError(f"stored object {key} is missing on read-back") from exc
    return size, digest.hexdigest(), None if kept is None else b"".join(kept)


def read_object(writer: RevisionWriter, key: str) -> bytes:
    """Read one small stored object (manifest, seal or token) completely."""
    with store_errors(f"read {key}"):
        data = _digest(writer, key)[2]
    if data is None:
        raise ValueError(f"{key} is not a seal file")
    return data


def read_back(writer: RevisionWriter, manifest: Manifest, seal_files: dict[str, bytes]) -> None:
    """Re-read every stored object and compare it with the manifest and the seal-file bytes.

    The stored key set must equal the listed files plus the three seal files exactly.
    """
    expected = {file.path for file in manifest.files} | set(SEAL_FILES)
    with store_errors("list stored objects"):
        stored = set(writer.keys())
    if stored != expected:
        missing = ", ".join(sorted(expected - stored)) or "none"
        extra = ", ".join(sorted(stored - expected)) or "none"
        raise VerificationFailedError(f"stored keys differ (missing: {missing}; extra: {extra})")
    for file in manifest.files:
        with store_errors(f"read back {file.path}"):
            size, sha256, _ = _digest(writer, file.path)
        if (size, sha256) != (file.size, file.sha256):
            raise VerificationFailedError(f"stored {file.path} differs from the manifest")
    for key, local in seal_files.items():
        with store_errors(f"read back {key}"):
            stored_bytes = _digest(writer, key)[2]
        if stored_bytes != local:
            raise VerificationFailedError(f"stored {key} differs from the sealed bytes")
