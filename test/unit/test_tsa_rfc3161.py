from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from zammad_pdf_archiver.domain.errors import PermanentError, TransientError

pytest.importorskip("pyhanko", reason="TSA adapter requires pyHanko")

from zammad_pdf_archiver.adapters.signing.tsa_rfc3161 import build_timestamper  # noqa: E402


def _tsa_req() -> Any:
    from asn1crypto import tsp  # type: ignore[import-untyped]

    return tsp.TimeStampReq(
        {
            "version": 1,
            "message_imprint": {
                "hash_algorithm": {"algorithm": "sha256"},
                "hashed_message": b"\x00" * 32,
            },
            "nonce": 1,
            "cert_req": True,
        }
    )


@dataclass(frozen=True)
class _DummyRfc3161:
    tsa_url: str
    timeout_seconds: float = 10.0
    ca_bundle_path: Path | None = None


@dataclass(frozen=True)
class _DummyTimestamp:
    enabled: bool
    rfc3161: _DummyRfc3161


@dataclass(frozen=True)
class _DummySigning:
    timestamp: _DummyTimestamp


@dataclass(frozen=True)
class _DummyTransport:
    trust_env: bool = False


@dataclass(frozen=True)
class _DummyHardening:
    transport: _DummyTransport = _DummyTransport()


@dataclass(frozen=True)
class _DummySettings:
    signing: _DummySigning
    hardening: _DummyHardening | None = None


@pytest.mark.parametrize("status", [500, 503, 599])
def test_tsa_http_5xx_is_transient(status: int) -> None:
    tsa_url = "https://tsa.test/rfc3161"
    settings = _DummySettings(
        signing=_DummySigning(
            timestamp=_DummyTimestamp(enabled=True, rfc3161=_DummyRfc3161(tsa_url=tsa_url))
        )
    )
    timestamper = build_timestamper(settings)

    with respx.mock:
        respx.post(tsa_url).mock(return_value=httpx.Response(status))
        with pytest.raises(TransientError, match="RFC3161 TSA returned HTTP"):
            asyncio.run(timestamper.async_request_tsa_response(_tsa_req()))


@pytest.mark.parametrize("status", [400, 401, 404])
def test_tsa_http_4xx_is_permanent(status: int) -> None:
    tsa_url = "https://tsa.test/rfc3161"
    settings = _DummySettings(
        signing=_DummySigning(
            timestamp=_DummyTimestamp(enabled=True, rfc3161=_DummyRfc3161(tsa_url=tsa_url))
        )
    )
    timestamper = build_timestamper(settings)

    with respx.mock:
        respx.post(tsa_url).mock(return_value=httpx.Response(status))
        with pytest.raises(PermanentError, match=f"RFC3161 TSA returned HTTP {status}"):
            asyncio.run(timestamper.async_request_tsa_response(_tsa_req()))


def test_tsa_wrong_content_type_is_permanent() -> None:
    tsa_url = "https://tsa.test/rfc3161"
    settings = _DummySettings(
        signing=_DummySigning(
            timestamp=_DummyTimestamp(enabled=True, rfc3161=_DummyRfc3161(tsa_url=tsa_url))
        )
    )
    timestamper = build_timestamper(settings)

    with respx.mock:
        respx.post(tsa_url).mock(
            return_value=httpx.Response(
                200,
                headers={"Content-Type": "text/plain"},
                content=b"not a tsp response",
            )
        )
        with pytest.raises(PermanentError, match="unexpected Content-Type"):
            asyncio.run(timestamper.async_request_tsa_response(_tsa_req()))


@pytest.mark.parametrize("trust_env", [False, True])
def test_tsa_http_client_respects_transport_trust_env(
    monkeypatch: pytest.MonkeyPatch, trust_env: bool
) -> None:
    tsa_url = "https://tsa.test/rfc3161"
    settings = _DummySettings(
        signing=_DummySigning(
            timestamp=_DummyTimestamp(enabled=True, rfc3161=_DummyRfc3161(tsa_url=tsa_url))
        ),
        hardening=_DummyHardening(transport=_DummyTransport(trust_env=trust_env)),
    )
    timestamper = build_timestamper(settings)

    captured: dict[str, Any] = {}

    class _DummyAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["trust_env"] = kwargs.get("trust_env")

        async def __aenter__(self) -> _DummyAsyncClient:
            return self

        async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return False

        async def post(
            self,
            url: str,
            *,
            content: bytes,
            headers: dict[str, str],
            **kwargs: Any,
        ) -> httpx.Response:  # noqa: ARG002
            return httpx.Response(500)

    monkeypatch.setattr(httpx, "AsyncClient", _DummyAsyncClient)

    with pytest.raises(TransientError, match="RFC3161 TSA returned HTTP 500"):
        asyncio.run(timestamper.async_request_tsa_response(_tsa_req()))

    assert captured["trust_env"] is trust_env
