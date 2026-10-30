"""Seal manifests, timestamp seals and check both offline against a synthetic PKI."""

from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from asn1crypto import cms
from cryptography.hazmat.primitives import hashes
from pyhanko.sign.timestamps.dummy_client import DummyTimeStamper
from pyhanko_certvalidator.registry import SimpleCertificateStore

from chronikwerk.failures import PermanentError
from chronikwerk.preservation.seal import (
    SealOptions,
    check_seal,
    check_timestamp,
    load_trust_anchors,
    seal_manifest,
    timestamp_seal,
)
from tests.support.pki import (
    PFX_PASSWORD,
    Identity,
    asn1_certificate,
    asn1_private_key,
    build_pki,
    pem_bundle,
    write_pkcs12,
)

MANIFEST = b'{"schema":"chronikwerk.manifest/1","ticket":{"number":"10042"}}\n'


def _seal_options(tmp_path: Path, identity: Identity | None = None) -> SealOptions:
    """Write a PFX holding the leaf and root, and return seal options for it."""
    pki = build_pki()
    leaf = identity or pki.seal
    path = write_pkcs12(tmp_path, leaf, (pki.root.certificate,))
    return SealOptions(pfx_path=path, pfx_password=PFX_PASSWORD)


def _anchors(tmp_path: Path, identity: Identity, name: str = "anchors.pem"):
    """Write the identity certificate as a PEM bundle and load it as trust anchors."""
    path = tmp_path / name
    path.write_bytes(pem_bundle(identity.certificate))
    return load_trust_anchors(path)


def _timestamper(
    fixed_dt: datetime | None = None,
    *,
    tsa: Identity | None = None,
    issuer: Identity | None = None,
) -> DummyTimeStamper:
    """Return an in-process timestamper backed by the synthetic (or the given) TSA identity."""
    pki = build_pki()
    tsa = tsa or pki.tsa
    issuer = issuer or pki.root
    return DummyTimeStamper(
        tsa_cert=asn1_certificate(tsa.certificate),
        tsa_key=asn1_private_key(tsa.key),
        certs_to_embed=SimpleCertificateStore.from_certs([asn1_certificate(issuer.certificate)]),
        fixed_dt=fixed_dt,
    )


def test_seal_then_check_is_ok(tmp_path) -> None:
    pki = build_pki()
    seal = asyncio.run(seal_manifest(MANIFEST, _seal_options(tmp_path)))

    check = asyncio.run(check_seal(MANIFEST, seal, trust_anchors=_anchors(tmp_path, pki.root)))

    assert check.ok
    assert check.intact and check.trusted
    assert check.signer_subject is not None and "Synthetic Manifest Seal" in check.signer_subject
    expected_fingerprint = pki.seal.certificate.fingerprint(hashes.SHA256()).hex()
    assert check.signer_sha256_fingerprint == expected_fingerprint
    assert check.signing_time is not None
    assert any("revocation" in problem for problem in check.problems)


def test_manifest_changed_by_one_byte_is_not_intact(tmp_path) -> None:
    pki = build_pki()
    seal = asyncio.run(seal_manifest(MANIFEST, _seal_options(tmp_path)))
    tampered = MANIFEST.replace(b"10042", b"10043")

    check = asyncio.run(check_seal(tampered, seal, trust_anchors=_anchors(tmp_path, pki.root)))

    assert not check.intact
    assert not check.ok
    assert "seal signature does not match the manifest" in check.problems


def test_untrusted_root_is_not_trusted(tmp_path) -> None:
    pki = build_pki()
    seal = asyncio.run(seal_manifest(MANIFEST, _seal_options(tmp_path)))

    check = asyncio.run(
        check_seal(MANIFEST, seal, trust_anchors=_anchors(tmp_path, pki.unrelated_root))
    )

    assert check.intact
    assert not check.trusted
    assert not check.ok
    assert any("does not chain to a trust anchor" in problem for problem in check.problems)


def test_expired_signer_is_refused(tmp_path) -> None:
    options = _seal_options(tmp_path, build_pki().expired_seal)

    with pytest.raises(PermanentError, match="expired"):
        asyncio.run(seal_manifest(MANIFEST, options))


def test_garbage_seal_is_not_intact(tmp_path) -> None:
    anchors = _anchors(tmp_path, build_pki().root)

    check = asyncio.run(check_seal(MANIFEST, b"not a CMS object", trust_anchors=anchors))

    assert not check.intact
    assert not check.ok
    assert check.problems and "malformed" in check.problems[0]


def test_timestamp_over_seal_checks_ok(tmp_path) -> None:
    pki = build_pki()
    seal = asyncio.run(seal_manifest(MANIFEST, _seal_options(tmp_path)))
    fixed = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)

    token = asyncio.run(timestamp_seal(seal, _timestamper(fixed)))
    check = asyncio.run(check_timestamp(seal, token, trust_anchors=_anchors(tmp_path, pki.root)))

    assert check.ok
    assert check.imprint_matches and check.intact and check.trusted
    assert check.gen_time == fixed
    assert check.tsa_subject is not None and "Time Stamping Authority" in check.tsa_subject


def test_token_over_different_bytes_does_not_match_imprint(tmp_path) -> None:
    pki = build_pki()
    seal = asyncio.run(seal_manifest(MANIFEST, _seal_options(tmp_path)))
    token = asyncio.run(timestamp_seal(b"some other seal", _timestamper()))

    check = asyncio.run(check_timestamp(seal, token, trust_anchors=_anchors(tmp_path, pki.root)))

    assert not check.imprint_matches
    assert check.intact
    assert not check.ok
    assert "timestamp token does not cover the seal" in check.problems


def test_garbage_token_is_not_intact(tmp_path) -> None:
    anchors = _anchors(tmp_path, build_pki().root)

    check = asyncio.run(check_timestamp(b"seal", b"\x30\x03garbage", trust_anchors=anchors))

    assert not check.intact
    assert not check.ok
    assert check.problems and "malformed" in check.problems[0]


def test_expected_signer_pin_matches(tmp_path) -> None:
    pki = build_pki()
    seal = asyncio.run(seal_manifest(MANIFEST, _seal_options(tmp_path)))
    fingerprint = pki.seal.certificate.fingerprint(hashes.SHA256()).hex()
    colon_form = ":".join(fingerprint[i : i + 2] for i in range(0, 64, 2)).upper()

    checks = [
        asyncio.run(
            check_seal(
                MANIFEST,
                seal,
                trust_anchors=_anchors(tmp_path, pki.root),
                expected_signer_sha256=pins,
            )
        )
        for pins in ([fingerprint], {"0" * 64, colon_form})
    ]

    for check in checks:
        assert check.ok
        assert not check.revocation_checked
        assert not any("expected signer" in problem for problem in check.problems)


def test_expected_signer_pin_mismatch_is_not_trusted(tmp_path) -> None:
    pki = build_pki()
    seal = asyncio.run(seal_manifest(MANIFEST, _seal_options(tmp_path)))

    check = asyncio.run(
        check_seal(
            MANIFEST,
            seal,
            trust_anchors=_anchors(tmp_path, pki.root),
            expected_signer_sha256=["0" * 64],
        )
    )

    assert check.intact
    assert not check.trusted
    assert not check.ok
    assert any("is not an expected signer" in problem for problem in check.problems)


def test_seal_and_timestamp_report_revocation_not_checked(tmp_path) -> None:
    pki = build_pki()
    seal = asyncio.run(seal_manifest(MANIFEST, _seal_options(tmp_path)))
    token = asyncio.run(timestamp_seal(seal, _timestamper()))
    anchors = _anchors(tmp_path, pki.root)

    seal_check = asyncio.run(check_seal(MANIFEST, seal, trust_anchors=anchors))
    token_check = asyncio.run(check_timestamp(seal, token, trust_anchors=anchors))

    assert seal_check.revocation_checked is False
    assert token_check.revocation_checked is False
    assert "revocation not checked (offline validation)" in seal_check.problems
    assert "revocation not checked (offline validation)" in token_check.problems


def test_parseable_but_tampered_token_is_not_intact(tmp_path) -> None:
    pki = build_pki()
    seal = asyncio.run(seal_manifest(MANIFEST, _seal_options(tmp_path)))
    token = asyncio.run(timestamp_seal(seal, _timestamper()))
    content_info = cms.ContentInfo.load(token)
    signer_info = content_info["content"]["signer_infos"][0]
    signature = bytearray(signer_info["signature"].native)
    signature[-1] ^= 0x01
    signer_info["signature"] = bytes(signature)
    tampered = content_info.dump(force=True)
    cms.ContentInfo.load(tampered, strict=True)

    check = asyncio.run(check_timestamp(seal, tampered, trust_anchors=_anchors(tmp_path, pki.root)))

    assert check.imprint_matches
    assert not check.intact
    assert not check.ok
    assert "timestamp token signature is not intact" in check.problems


def test_token_from_untrusted_tsa_is_not_trusted(tmp_path) -> None:
    pki = build_pki()
    seal = asyncio.run(seal_manifest(MANIFEST, _seal_options(tmp_path)))
    stranger = _timestamper(tsa=pki.unrelated_tsa, issuer=pki.unrelated_root)
    token = asyncio.run(timestamp_seal(seal, stranger))

    check = asyncio.run(check_timestamp(seal, token, trust_anchors=_anchors(tmp_path, pki.root)))

    assert check.imprint_matches and check.intact
    assert not check.trusted
    assert not check.ok
    assert any("TSA certificate does not chain" in problem for problem in check.problems)


def test_token_signed_without_timestamping_eku_is_not_trusted(tmp_path) -> None:
    pki = build_pki()
    seal = asyncio.run(seal_manifest(MANIFEST, _seal_options(tmp_path)))
    token = asyncio.run(timestamp_seal(seal, _timestamper(tsa=pki.seal)))

    check = asyncio.run(check_timestamp(seal, token, trust_anchors=_anchors(tmp_path, pki.root)))

    assert check.imprint_matches
    assert not check.trusted
    assert not check.ok


@pytest.mark.parametrize("algorithm", ["md5", "sha1"])
def test_weak_imprint_algorithms_are_rejected(tmp_path, algorithm: str) -> None:
    pki = build_pki()
    seal = asyncio.run(seal_manifest(MANIFEST, _seal_options(tmp_path)))
    digest = hashlib.new(algorithm, seal).digest()
    token = bytes(asyncio.run(_timestamper().async_timestamp(digest, algorithm)).dump())

    check = asyncio.run(check_timestamp(seal, token, trust_anchors=_anchors(tmp_path, pki.root)))

    assert not check.ok
    assert not check.imprint_matches
    assert check.problems == (
        f"timestamp imprint algorithm '{algorithm}' is not accepted "
        "(accepted: sha256, sha384, sha512)",
    )


def test_sha512_imprint_is_accepted(tmp_path) -> None:
    pki = build_pki()
    seal = asyncio.run(seal_manifest(MANIFEST, _seal_options(tmp_path)))
    digest = hashlib.sha512(seal).digest()
    token = bytes(asyncio.run(_timestamper().async_timestamp(digest, "sha512")).dump())

    check = asyncio.run(check_timestamp(seal, token, trust_anchors=_anchors(tmp_path, pki.root)))

    assert check.ok


def _openssl_3_or_later() -> str | None:
    """Return the openssl binary path when it reports OpenSSL 3.x or later."""
    binary = shutil.which("openssl")
    if binary is None:
        return None
    result = subprocess.run([binary, "version"], capture_output=True, text=True, check=False)
    match = re.match(r"OpenSSL (\d+)\.", result.stdout)
    return binary if match and int(match.group(1)) >= 3 else None


def test_openssl_verifies_seal_and_timestamp(tmp_path) -> None:
    openssl = _openssl_3_or_later()
    if openssl is None:
        pytest.skip("OpenSSL 3.x or later is not on PATH (LibreSSL or missing openssl)")
    pki = build_pki()
    seal = asyncio.run(seal_manifest(MANIFEST, _seal_options(tmp_path)))
    token = asyncio.run(timestamp_seal(seal, _timestamper()))
    (tmp_path / "manifest.json").write_bytes(MANIFEST)
    (tmp_path / "manifest.json.p7s").write_bytes(seal)
    (tmp_path / "manifest.json.p7s.tst").write_bytes(token)
    (tmp_path / "hfmt-seal-ca.pem").write_bytes(pem_bundle(pki.root.certificate))
    (tmp_path / "tsa-ca.pem").write_bytes(pem_bundle(pki.root.certificate))
    commands = (
        [openssl, "cms", "-verify", "-binary", "-inform", "DER", "-purpose", "any"]
        + ["-in", "manifest.json.p7s", "-content", "manifest.json"]
        + ["-CAfile", "hfmt-seal-ca.pem", "-out", "/dev/null"],
        [openssl, "ts", "-verify", "-token_in", "-in", "manifest.json.p7s.tst"]
        + ["-data", "manifest.json.p7s", "-CAfile", "tsa-ca.pem"],
    )
    for command in commands:
        result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, check=False)
        assert result.returncode == 0, (command, result.stdout, result.stderr)
