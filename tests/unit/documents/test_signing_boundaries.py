"""Cover document-signing boundaries with local injected dependencies."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import chronikwerk.archiving.rendering as rendering
import chronikwerk.documents.signing as signing_module
from chronikwerk.documents.options import DocumentOptions, SigningOptions, TimestampOptions
from chronikwerk.documents.signing import SignedPdf, sign_pdf_with_provenance
from chronikwerk.failures import PermanentError, TransientError


def _signing_options(*, enabled: bool) -> SigningOptions:
    """Build signing options without reading certificate material."""
    return SigningOptions(
        enabled=enabled,
        pfx_path=Path("/not-read-in-this-test.pfx") if enabled else None,
        pfx_password=None,
        reason="Archive",
        location="Datacenter",
        timestamp=TimestampOptions(
            enabled=False,
            tsa_url=None,
            timeout_seconds=5.0,
            ca_bundle_path=None,
            user=None,
            password=None,
            trust_env=False,
            allow_insecure_http=False,
            allow_private_networks=False,
        ),
    )


@pytest.mark.parametrize("enabled", [False, True])
def test_rendering_only_signs_when_enabled(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    snapshot = SimpleNamespace(articles=(), articles_total=None, articles_omitted=0)
    signing = _signing_options(enabled=enabled)
    options = DocumentOptions(
        max_articles=0,
        article_limit_mode="render_all",
        locale="en-GB",
        timezone="UTC",
        signing=signing,
    )
    signed_inputs: list[bytes] = []

    async def build_snapshot(*_args: object, **_kwargs: object) -> object:
        return snapshot

    async def render_pdf(*_args: object, **_kwargs: object) -> bytes:
        return b"unsigned-pdf"

    def sign(pdf_bytes: bytes, *, signing: SigningOptions) -> SignedPdf:
        signed_inputs.append(pdf_bytes)
        assert signing.enabled is True
        return SignedPdf(pdf_bytes=b"signed-pdf", certificate_fingerprint="fingerprint")

    monkeypatch.setattr(rendering, "build_snapshot", build_snapshot)
    monkeypatch.setattr(rendering, "render_pdf", render_pdf)
    monkeypatch.setattr(rendering, "sign_pdf_with_provenance", sign)

    rendered = asyncio.run(
        rendering.build_and_render_pdf(
            client=object(),  # type: ignore[arg-type]
            ticket_id=42,
            ticket=object(),  # type: ignore[arg-type]
            tags=object(),  # type: ignore[arg-type]
            options=options,
        )
    )

    assert signed_inputs == ([b"unsigned-pdf"] if enabled else [])
    assert rendered.pdf_bytes == (b"signed-pdf" if enabled else b"unsigned-pdf")
    assert rendered.signing_cert_fingerprint == ("fingerprint" if enabled else None)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (httpx.ConnectError("TSA unavailable"), TransientError),
        (ValueError("bad PDF"), PermanentError),
    ],
)
def test_public_signing_boundary_classifies_temporary_and_document_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected: type[Exception],
) -> None:
    class _Writer:
        def __init__(self, _stream: object) -> None:
            pass

    class _FailingSigner:
        def sign_pdf(self, _writer: object, *, output: object) -> None:
            del output
            raise failure

    session = SimpleNamespace(
        pdf_signer=_FailingSigner(), writer_type=_Writer, certificate_fingerprint="unused"
    )
    monkeypatch.setattr(signing_module, "_load_pfx", lambda _options: object())
    monkeypatch.setattr(signing_module, "_build_pdf_signing_session", lambda *_args: session)

    with pytest.raises(expected):
        sign_pdf_with_provenance(b"minimal PDF input", _signing_options(enabled=True))
