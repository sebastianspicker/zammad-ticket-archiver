"""Shared HTTP transport utilities for bounded upstream requests."""

from __future__ import annotations

from typing import Any

import httpx


class ResponseBodyTooLargeError(ValueError):
    """Raised when an upstream response exceeds its configured in-memory limit."""


class UnsupportedResponseEncodingError(ValueError):
    """Raised before reading a compressed response that could expand beyond the byte limit."""


def buffered_response(response: httpx.Response, content: bytes) -> httpx.Response:
    """Clone a streamed response with its bounded body materialized in memory."""
    return httpx.Response(
        response.status_code,
        headers=response.headers,
        content=content,
        request=response.request,
        extensions=response.extensions,
    )


def pin_request_url(
    url: httpx.URL,
    resolved_address: str | None,
) -> tuple[httpx.URL, dict[str, str], dict[str, Any]]:
    """Pin a validated address while preserving HTTP Host and TLS SNI identity."""
    if resolved_address is None:
        return url, {}, {}
    return (
        url.copy_with(host=resolved_address),
        {"Host": url.netloc.decode("ascii")},
        {"sni_hostname": url.host},
    )


async def read_response_body_limited(response: httpx.Response, *, max_bytes: int) -> bytes:
    """Read a streamed response without allowing its decoded body to exceed ``max_bytes``."""
    _preflight_response_body_limit(response, max_bytes=max_bytes)

    body = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
        if len(body) + len(chunk) > max_bytes:
            raise ResponseBodyTooLargeError(f"upstream response exceeds {max_bytes}-byte limit")
        body.extend(chunk)
    return bytes(body)


def _preflight_response_body_limit(response: httpx.Response, *, max_bytes: int) -> None:
    """Reject unsupported encodings and oversized parseable declared response lengths."""
    content_encoding = (response.headers.get("Content-Encoding") or "identity").strip().lower()
    if content_encoding not in {"", "identity"}:
        raise UnsupportedResponseEncodingError(
            f"upstream response uses unsupported Content-Encoding {content_encoding!r}"
        )

    content_length = response.headers.get("Content-Length")
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > max_bytes:
            raise ResponseBodyTooLargeError(
                f"upstream response declares {declared_length} bytes; limit is {max_bytes} bytes"
            )


def timeouts_for(seconds: float) -> httpx.Timeout:
    """Build httpx.Timeout with bounded connect/pool for fail-fast on unreachable upstreams."""
    total = float(seconds)
    connect = min(5.0, total)
    return httpx.Timeout(connect=connect, read=total, write=total, pool=connect)
