"""Test RFC 3161 configuration and status classification without a network."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from chronikwerk.documents.options import TimestampOptions
from chronikwerk.documents.tsa import build_timestamper
from chronikwerk.failures import PermanentError, TransientError


def _timestamp_options(*, tsa_url: str | None) -> TimestampOptions:
    """Build isolated timestamp options for one boundary scenario."""
    return TimestampOptions(
        enabled=True,
        tsa_url=tsa_url,
        timeout_seconds=5.0,
        ca_bundle_path=None,
        user=None,
        password=None,
        trust_env=False,
        allow_insecure_http=False,
        allow_private_networks=False,
    )


def test_timestamper_requires_a_url_when_timestamping_is_enabled() -> None:
    with pytest.raises(PermanentError, match="TSA URL is missing"):
        build_timestamper(_timestamp_options(tsa_url=None))


@pytest.mark.parametrize(
    ("status", "expected"),
    [(503, TransientError), (400, PermanentError)],
)
def test_timestamper_maps_http_statuses_without_contacting_a_tsa(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    expected: type[Exception],
) -> None:
    timestamper = build_timestamper(_timestamp_options(tsa_url="https://tsa.example/rfc3161"))

    async def post_request(_request: object) -> httpx.Response:
        response = httpx.Response(status)
        timestamper._validate_http_response(response)  # type: ignore[attr-defined]
        return response

    monkeypatch.setattr(timestamper, "_post_tsa_request", post_request)

    with pytest.raises(expected, match=f"HTTP {status}"):
        asyncio.run(timestamper.async_request_tsa_response(object()))
