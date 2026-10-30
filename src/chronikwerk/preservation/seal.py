"""Seal archive manifests with detached CMS signatures and RFC 3161 timestamps."""

from __future__ import annotations

import hashlib
from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from asn1crypto import cms, tsp
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from pyhanko.sign.timestamps.api import TimeStamper
from pyhanko.sign.validation.generic_cms import (
    async_validate_detached_cms,
    validate_tst_signed_data,
)
from pyhanko.sign.validation.status import TimestampSignatureStatus
from pyhanko_certvalidator import ValidationContext

from chronikwerk.documents.signing import _get_cached_signer, _PfxMaterial
from chronikwerk.failures import PermanentError, TransientError

_SEAL_DIGEST_ALGORITHM = "sha256"
_REVOCATION_NOT_CHECKED = "revocation not checked (offline validation)"
# Validation runs offline in soft-fail mode without fetching, so revocation is never checked.
_REVOCATION_CHECKED = False
_ACCEPTED_IMPRINT_ALGORITHMS = frozenset({"sha256", "sha384", "sha512"})


@dataclass(frozen=True, slots=True)
class SealOptions:
    """Locate the PKCS#12/PFX identity that seals archive manifests."""

    pfx_path: Path
    pfx_password: str | None


@dataclass(frozen=True, slots=True)
class SealCheck:
    """Report whether a detached manifest seal is intact and chains to a trust anchor."""

    intact: bool
    trusted: bool
    signer_subject: str | None
    signer_sha256_fingerprint: str | None
    signing_time: datetime | None
    problems: tuple[str, ...]
    revocation_checked: bool = _REVOCATION_CHECKED

    @property
    def ok(self) -> bool:
        """Return whether the seal is both intact and trusted."""
        return self.intact and self.trusted


@dataclass(frozen=True, slots=True)
class TimestampCheck:
    """Report whether a timestamp token covers the seal and chains to a trust anchor."""

    imprint_matches: bool
    intact: bool
    trusted: bool
    gen_time: datetime | None
    tsa_subject: str | None
    problems: tuple[str, ...]
    revocation_checked: bool = _REVOCATION_CHECKED

    @property
    def ok(self) -> bool:
        """Return whether the token covers the seal, is intact and is trusted."""
        return self.imprint_matches and self.intact and self.trusted


def _load_seal_pfx(options: SealOptions) -> _PfxMaterial:
    path = Path(options.pfx_path)
    if not path.exists() or not path.is_file():
        raise PermanentError(f"Seal PFX file not found: {path}")
    password = options.pfx_password.encode("utf-8") if options.pfx_password else None
    return _PfxMaterial(path=path, pfx_bytes=path.read_bytes(), password=password)


def _classify_failure(exc: Exception, action: str) -> PermanentError | TransientError:
    if isinstance(
        exc,
        httpx.TimeoutException | httpx.ConnectError | ConnectionError | OSError | TimeoutError,
    ):
        return TransientError(f"Failed to {action} due to temporary (TSA) network issue")
    return PermanentError(f"Failed to {action}")


async def seal_manifest(manifest_bytes: bytes, options: SealOptions) -> bytes:
    """
    Return a DER detached CMS SignedData seal (SHA-256, CAdES-B) over the manifest bytes.

    The full certificate chain from the PFX bundle is embedded. Expired or not-yet-valid
    signer certificates are refused with PermanentError.
    """
    if not isinstance(manifest_bytes, bytes | bytearray) or not manifest_bytes:
        raise ValueError("manifest_bytes must be non-empty bytes")

    signer = _get_cached_signer(_load_seal_pfx(options)).signer
    try:
        content_info = await signer.async_sign_general_data(
            bytes(manifest_bytes), _SEAL_DIGEST_ALGORITHM, detached=True, use_cades=True
        )
    except TransientError, PermanentError:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _classify_failure(exc, "seal manifest") from exc
    return bytes(content_info.dump())


async def timestamp_seal(seal_bytes: bytes, timestamper: TimeStamper) -> bytes:
    """Return a DER RFC 3161 TimeStampToken ContentInfo over sha256(seal_bytes)."""
    if not isinstance(seal_bytes, bytes | bytearray) or not seal_bytes:
        raise ValueError("seal_bytes must be non-empty bytes")

    digest = hashlib.sha256(bytes(seal_bytes)).digest()
    try:
        token = await timestamper.async_timestamp(digest, _SEAL_DIGEST_ALGORITHM)
    except TransientError, PermanentError:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        raise _classify_failure(exc, "timestamp seal") from exc
    return bytes(token.dump())


def load_trust_anchors(path: Path) -> tuple[asn1_x509.Certificate, ...]:
    """Load a PEM bundle of trust anchor certificates; reject missing or empty bundles."""
    anchors_path = Path(path)
    if not anchors_path.exists() or not anchors_path.is_file():
        raise PermanentError(f"Trust anchor bundle not found: {anchors_path}")
    try:
        certificates = x509.load_pem_x509_certificates(anchors_path.read_bytes())
    except ValueError as exc:
        raise PermanentError(f"Trust anchor bundle is not valid PEM: {anchors_path}") from exc
    return tuple(
        asn1_x509.Certificate.load(certificate.public_bytes(Encoding.DER))
        for certificate in certificates
    )


def _validation_context(
    trust_anchors: tuple[asn1_x509.Certificate, ...] | list[asn1_x509.Certificate],
    validation_time: datetime | None,
) -> ValidationContext:
    return ValidationContext(
        trust_roots=list(trust_anchors),
        allow_fetching=False,
        revocation_mode="soft-fail",
        moment=validation_time,
    )


def _load_signed_data(der_bytes: bytes) -> cms.SignedData:
    content_info = cms.ContentInfo.load(bytes(der_bytes), strict=True)
    if content_info["content_type"].native != "signed_data":
        raise ValueError("CMS ContentInfo does not contain SignedData")
    return content_info["content"]


def _trust_problems(status: Any, role: str) -> list[str]:
    problems: list[str] = []
    if status.revoked:
        problems.append(f"{role} certificate is revoked")
    elif not status.trusted:
        indication = status.trust_problem_indic
        reason = f" ({indication.name})" if indication is not None else ""
        problems.append(f"{role} certificate does not chain to a trust anchor{reason}")
    if not _REVOCATION_CHECKED:
        problems.append(_REVOCATION_NOT_CHECKED)
    return problems


def _normalize_fingerprint(value: str) -> str:
    return value.replace(":", "").strip().lower()


def _pin_problem(fingerprint: str, expected: Collection[str] | None) -> str | None:
    """Return a problem when ``expected`` is given and does not contain ``fingerprint``."""
    if expected is None:
        return None
    if fingerprint in {_normalize_fingerprint(value) for value in expected}:
        return None
    return f"signer certificate SHA-256 fingerprint {fingerprint} is not an expected signer"


async def check_seal(
    manifest_bytes: bytes,
    seal_bytes: bytes,
    *,
    trust_anchors: tuple[asn1_x509.Certificate, ...] | list[asn1_x509.Certificate],
    validation_time: datetime | None = None,
    expected_signer_sha256: Collection[str] | None = None,
) -> SealCheck:
    """Validate a detached manifest seal offline against the given trust anchors.

    When ``expected_signer_sha256`` is given, the seal is trusted only if the signer
    certificate's SHA-256 fingerprint (hex, colons and case ignored) is one of them.
    """
    try:
        signed_data = _load_signed_data(seal_bytes)
        status = await async_validate_detached_cms(
            bytes(manifest_bytes),
            signed_data,
            signer_validation_context=_validation_context(trust_anchors, validation_time),
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return SealCheck(
            intact=False,
            trusted=False,
            signer_subject=None,
            signer_sha256_fingerprint=None,
            signing_time=None,
            problems=(f"seal is malformed or unreadable: {type(exc).__name__}: {exc}",),
        )

    intact = bool(status.intact and status.valid)
    fingerprint = status.signing_cert.sha256.hex()
    problems = [] if intact else ["seal signature does not match the manifest"]
    pin_problem = _pin_problem(fingerprint, expected_signer_sha256)
    if pin_problem is not None:
        problems.append(pin_problem)
    problems.extend(_trust_problems(status, "signer"))
    return SealCheck(
        intact=intact,
        trusted=bool(status.trusted) and pin_problem is None,
        signer_subject=status.signing_cert.subject.human_friendly,
        signer_sha256_fingerprint=fingerprint,
        signing_time=status.signer_reported_dt,
        problems=tuple(problems),
    )


def _token_imprint(signed_data: cms.SignedData) -> tuple[str, bytes]:
    tst_info = signed_data["encap_content_info"]["content"].parsed
    if not isinstance(tst_info, tsp.TSTInfo):
        raise ValueError("SignedData does not encapsulate TSTInfo")
    imprint = tst_info["message_imprint"]
    return imprint["hash_algorithm"]["algorithm"].native, imprint["hashed_message"].native


def _failed_timestamp(problem: str) -> TimestampCheck:
    return TimestampCheck(
        imprint_matches=False,
        intact=False,
        trusted=False,
        gen_time=None,
        tsa_subject=None,
        problems=(problem,),
    )


def _malformed_timestamp(exc: Exception) -> TimestampCheck:
    return _failed_timestamp(
        f"timestamp token is malformed or unreadable: {type(exc).__name__}: {exc}"
    )


def _rejected_algorithm(algorithm: str) -> TimestampCheck:
    accepted = ", ".join(sorted(_ACCEPTED_IMPRINT_ALGORITHMS))
    return _failed_timestamp(
        f"timestamp imprint algorithm {algorithm!r} is not accepted (accepted: {accepted})"
    )


async def check_timestamp(
    seal_bytes: bytes,
    token_bytes: bytes,
    *,
    trust_anchors: tuple[asn1_x509.Certificate, ...] | list[asn1_x509.Certificate],
    validation_time: datetime | None = None,
) -> TimestampCheck:
    """Validate an RFC 3161 token over the seal offline against the given trust anchors."""
    try:
        signed_data = _load_signed_data(token_bytes)
        algorithm, token_imprint = _token_imprint(signed_data)
        if algorithm not in _ACCEPTED_IMPRINT_ALGORITHMS:
            return _rejected_algorithm(algorithm)
        imprint_matches = hashlib.new(algorithm, bytes(seal_bytes)).digest() == token_imprint
        # Validate the token against its own imprint so `intact` reports only the
        # token's cryptographic integrity; coverage of the seal is `imprint_matches`.
        status_kwargs = await validate_tst_signed_data(
            signed_data,
            _validation_context(trust_anchors, validation_time),
            token_imprint,
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return _malformed_timestamp(exc)

    status = TimestampSignatureStatus(**status_kwargs)
    intact = bool(status.intact and status.valid)
    problems = [] if imprint_matches else ["timestamp token does not cover the seal"]
    if not intact:
        problems.append("timestamp token signature is not intact")
    problems.extend(_trust_problems(status, "TSA"))
    return TimestampCheck(
        imprint_matches=imprint_matches,
        intact=intact,
        trusted=bool(status.trusted),
        gen_time=status.timestamp,
        tsa_subject=status.signing_cert.subject.human_friendly,
        problems=tuple(problems),
    )


_PROBE_MANIFEST = b'{"probe": "chronikwerk seal identity check"}'


async def seal_identity_problems(
    options: SealOptions,
    trust_anchors: tuple[asn1_x509.Certificate, ...] | None,
) -> list[str]:
    """Seal a probe document and return why the identity cannot produce trusted seals.

    Without ``trust_anchors`` only loading and signing are checked. The offline note that
    revocation was not checked is not a problem here.
    """
    try:
        seal_bytes = await seal_manifest(_PROBE_MANIFEST, options)
    except (PermanentError, TransientError) as exc:
        return [str(exc)]
    if trust_anchors is None:
        return []
    check = await check_seal(_PROBE_MANIFEST, seal_bytes, trust_anchors=trust_anchors)
    if check.ok:
        return []
    return [problem for problem in check.problems if problem != _REVOCATION_NOT_CHECKED]
