"""Enforce the shared outbound HTTP and DNS trust boundary, including bounded response reading."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import ParseResult, urlparse

import httpx


class OutboundPolicyError(ValueError):
    """Base error for outbound URL policy evaluation."""


class OutboundPolicyPermanentError(OutboundPolicyError):
    """The configured outbound destination is permanently unsafe or invalid."""


class OutboundPolicyTransientError(OutboundPolicyError):
    """Outbound policy evaluation could not complete because DNS is unavailable."""


_PERMANENT_DNS_ERRORS = frozenset(
    error
    for name in ("EAI_NONAME", "EAI_NODATA", "EAI_BADFLAGS", "EAI_FAMILY", "EAI_SERVICE")
    if isinstance(error := getattr(socket, name, None), int)
)
_LOCALHOST_NAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "localhost6",
        "localhost6.localdomain",
        "ip6-localhost",
    }
)


def _host_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def _validate_ip(host: str, *, allow_private_networks: bool) -> None:
    address = _host_ip(host)
    if address is not None and not allow_private_networks and not address.is_global:
        raise OutboundPolicyPermanentError("Outbound URL targets a non-global address")


def _is_localhost_name(host: str) -> bool:
    return host in _LOCALHOST_NAMES or host.endswith(".localhost")


def _require_url_origin(parsed: ParseResult) -> tuple[str, str]:
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if scheme not in {"https", "http"} or hostname is None:
        raise OutboundPolicyPermanentError("Outbound URL must include an https:// host")
    if parsed.username is not None or parsed.password is not None:
        raise OutboundPolicyPermanentError("Outbound URL must not include credentials")
    return scheme, hostname


def _require_allowed_scheme(scheme: str, *, allow_insecure_http: bool) -> None:
    if scheme == "http" and not allow_insecure_http:
        raise OutboundPolicyPermanentError("Plain HTTP upstream URL is not allowed")


def _require_allowed_host(host: str, *, allow_private_networks: bool) -> None:
    if _is_localhost_name(host) and not allow_private_networks:
        raise OutboundPolicyPermanentError("Localhost outbound URL is not allowed")
    _validate_ip(host, allow_private_networks=allow_private_networks)


def _validated_url_host(
    url: str,
    *,
    allow_insecure_http: bool,
    allow_private_networks: bool,
) -> tuple[ParseResult, str]:
    parsed = urlparse(url)
    scheme, hostname = _require_url_origin(parsed)
    _require_allowed_scheme(scheme, allow_insecure_http=allow_insecure_http)
    host = hostname.rstrip(".").lower()
    _require_allowed_host(host, allow_private_networks=allow_private_networks)
    return parsed, host


def _resolved_addresses(host: str, port: int) -> tuple[str, ...]:
    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        if exc.errno in _PERMANENT_DNS_ERRORS:
            raise OutboundPolicyPermanentError("Outbound hostname could not be resolved") from exc
        raise OutboundPolicyTransientError(
            "Outbound DNS resolver is temporarily unavailable"
        ) from exc
    except OSError as exc:
        raise OutboundPolicyTransientError(
            "Outbound DNS resolver is temporarily unavailable"
        ) from exc

    addresses = tuple(dict.fromkeys(str(item[4][0]) for item in results if item[4]))
    if not addresses:
        raise OutboundPolicyPermanentError("Outbound hostname resolved to no addresses")
    return addresses


def validate_url_policy(
    url: str,
    *,
    allow_insecure_http: bool = False,
    allow_private_networks: bool = False,
    resolve_dns: bool = False,
) -> tuple[str, ...]:
    """Validate URL transport and, optionally, every DNS-resolved address."""
    parsed, host = _validated_url_host(
        url,
        allow_insecure_http=allow_insecure_http,
        allow_private_networks=allow_private_networks,
    )
    literal_address = _host_ip(host)
    if literal_address is not None:
        return (str(literal_address),)
    if allow_private_networks or not resolve_dns:
        return ()

    addresses = _resolved_addresses(
        host,
        parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
    )
    for address in addresses:
        _validate_ip(address, allow_private_networks=False)
    return addresses


async def validate_url_policy_async(
    url: str,
    *,
    allow_insecure_http: bool = False,
    allow_private_networks: bool = False,
    timeout_seconds: float = 5.0,
) -> str | None:
    """Validate and return one safe address that callers can pin for connection."""
    try:
        addresses = await asyncio.wait_for(
            asyncio.to_thread(
                validate_url_policy,
                url,
                allow_insecure_http=allow_insecure_http,
                allow_private_networks=allow_private_networks,
                resolve_dns=True,
            ),
            timeout=max(0.001, float(timeout_seconds)),
        )
    except TimeoutError as exc:
        raise OutboundPolicyTransientError("Outbound DNS resolution timed out") from exc
    return addresses[0] if addresses else None


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
    preflight_response_body_limit(response, max_bytes=max_bytes)

    body = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
        if len(body) + len(chunk) > max_bytes:
            raise ResponseBodyTooLargeError(f"upstream response exceeds {max_bytes}-byte limit")
        body.extend(chunk)
    return bytes(body)


def preflight_response_body_limit(response: httpx.Response, *, max_bytes: int) -> None:
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
