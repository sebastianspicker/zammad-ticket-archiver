"""Verify signed webhook requests before they reach web routes."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from chronikwerk.configuration.models import Settings
from chronikwerk.web.constants import DELIVERY_ID_HEADER, INGEST_PROTECTED_PATHS
from chronikwerk.web.responses import api_error

_SIGNATURE_HEADER = "X-Hub-Signature"
_ALGORITHMS: dict[str, tuple[int, Any]] = {
    "sha256": (hashlib.sha256().digest_size, hashlib.sha256),
}
_STRICT_SIGNATURE_DOMAIN = b"zammad-webhook-v1\0"


@dataclass(frozen=True, slots=True)
class _VerificationContext:
    """Validated signature details and ASGI request handles for verification."""

    signature: bytes
    digest_ctor: type
    signature_prefix: bytes
    receive: Receive
    scope: Scope
    send: Send


def _secret_bytes(settings: Settings | None) -> bytes | None:
    if settings is None:
        return None
    secret = settings.zammad.webhook_hmac_secret
    if secret is None:
        return None
    value = secret.get_secret_value()
    if value and value.strip():
        return value.encode("utf-8")
    return None


def _forbidden() -> JSONResponse:
    return api_error(403, "forbidden", code="forbidden")


def _service_misconfigured() -> JSONResponse:
    return api_error(503, "webhook_auth_not_configured", code="webhook_auth_not_configured")


def _missing_delivery_id() -> JSONResponse:
    return api_error(400, "missing_delivery_id", code="missing_delivery_id")


def _normalized_delivery_id(value: str | None) -> str | None:
    return (value or "").strip() or None


def _strict_signature_prefix(delivery_id: str) -> bytes:
    """Return the strict-mode MAC prefix before the raw request body.

    Strict-mode MAC bytes are ``domain || uint64_be(len(id_utf8)) || id_utf8 || body``.
    The domain and length prefix make the normalized delivery ID/body boundary unambiguous.
    """
    delivery_id_bytes = delivery_id.encode("utf-8")
    return (
        _STRICT_SIGNATURE_DOMAIN
        + len(delivery_id_bytes).to_bytes(8, byteorder="big")
        + delivery_id_bytes
    )


async def _send_rejection(
    response: JSONResponse,
    scope: Scope,
    receive: Receive,
    send: Send,
) -> None:
    response.headers["Connection"] = "close"
    await response(scope, receive, send)


def _parse_signature(value: str) -> tuple[bytes, type, str] | None:
    """Parse X-Hub-Signature (sha256=<hex>)."""
    try:
        algorithm, hex_digest = value.strip().split("=", 1)
    except ValueError:
        return None

    algo_lower = algorithm.lower()
    algorithm_spec = _ALGORITHMS.get(algo_lower)
    if algorithm_spec is None:
        return None

    digest_size, digest_ctor = algorithm_spec
    try:
        digest = bytes.fromhex(hex_digest)
    except ValueError:
        return None

    if len(digest) != digest_size:
        return None
    return (digest, digest_ctor, algo_lower)


async def _read_body(
    receive: Receive, *, on_chunk: Callable[[bytes], None]
) -> tuple[list[bytes], bool]:
    """Read request body while updating the MAC."""
    chunks: list[bytes] = []
    while True:
        message = await receive()
        message_type = message.get("type")
        if message_type == "http.disconnect":
            return (chunks, True)
        if message_type != "http.request":
            continue

        body = message.get("body", b"")
        if body:
            chunks.append(body)
            on_chunk(body)

        if not message.get("more_body", False):
            return (chunks, False)


def _replay_receive(chunks: list[bytes]) -> Receive:
    idx = 0

    async def receive() -> Message:
        """Replay buffered chunks so downstream handlers receive the original body."""
        nonlocal idx
        if idx >= len(chunks):
            return {"type": "http.request", "body": b"", "more_body": False}

        body = chunks[idx]
        idx += 1
        return {"type": "http.request", "body": body, "more_body": idx < len(chunks)}

    return receive


class HmacVerifyMiddleware:
    """Reject unauthenticated webhook traffic before route processing."""

    def __init__(self, app: ASGIApp, *, settings: Settings | None) -> None:
        self.app = app
        self._secret = _secret_bytes(settings)
        self._require_delivery_id = (
            settings.hardening.webhook.require_delivery_id if settings is not None else False
        )

    async def _reject_missing_delivery_id(
        self,
        headers: Headers,
        receive: Receive,
        scope: Scope,
        send: Send,
    ) -> bool:
        if not self._require_delivery_id:
            return False
        if _normalized_delivery_id(headers.get(DELIVERY_ID_HEADER)) is not None:
            return False

        await _send_rejection(_missing_delivery_id(), scope, receive, send)
        return True

    async def _reject_without_secret(self, scope: Scope, receive: Receive, send: Send) -> bool:
        if self._secret:
            return False

        await _send_rejection(_service_misconfigured(), scope, receive, send)
        return True

    async def _parse_request_signature(
        self,
        headers: Headers,
        receive: Receive,
        scope: Scope,
        send: Send,
    ) -> _VerificationContext | None:
        signature_raw = headers.get(_SIGNATURE_HEADER)
        if not signature_raw:
            await _send_rejection(_forbidden(), scope, receive, send)
            return None

        parsed = _parse_signature(signature_raw)
        if parsed is None:
            await _send_rejection(_forbidden(), scope, receive, send)
            return None

        signature, digest_ctor, _algo_name = parsed
        return _VerificationContext(
            signature=signature,
            digest_ctor=digest_ctor,
            signature_prefix=self._signature_prefix(headers),
            receive=receive,
            scope=scope,
            send=send,
        )

    def _signature_prefix(self, headers: Headers) -> bytes:
        delivery_id = _normalized_delivery_id(headers.get(DELIVERY_ID_HEADER))
        if self._require_delivery_id and delivery_id is not None:
            return _strict_signature_prefix(delivery_id)
        return b""

    async def _verify_body_signature(
        self,
        verification: _VerificationContext,
    ) -> list[bytes] | None:
        if self._secret is None:
            await _send_rejection(
                _service_misconfigured(),
                verification.scope,
                verification.receive,
                verification.send,
            )
            return None
        mac = hmac.new(self._secret, digestmod=verification.digest_ctor)
        mac.update(verification.signature_prefix)
        chunks, disconnected = await _read_body(verification.receive, on_chunk=mac.update)
        if disconnected:
            await _forbidden()(verification.scope, verification.receive, verification.send)
            return None

        if not hmac.compare_digest(verification.signature, mac.digest()):
            await _forbidden()(verification.scope, verification.receive, verification.send)
            return None

        return chunks

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("method") != "POST" or scope.get("path") not in INGEST_PROTECTED_PATHS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if await self._reject_missing_delivery_id(headers, receive, scope, send):
            return
        if await self._reject_without_secret(scope, receive, send):
            return

        parsed = await self._parse_request_signature(headers, receive, scope, send)
        if parsed is None:
            return

        chunks = await self._verify_body_signature(parsed)
        if chunks is None:
            return

        await self.app(scope, _replay_receive(chunks), send)
