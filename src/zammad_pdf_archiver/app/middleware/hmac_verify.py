from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from typing import Any

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from zammad_pdf_archiver.adapters.http_util import drain_stream
from zammad_pdf_archiver.app.constants import DELIVERY_ID_HEADER
from zammad_pdf_archiver.app.responses import api_error
from zammad_pdf_archiver.config.settings import Settings

_SIGNATURE_HEADER = "X-Hub-Signature"
_INGEST_PATH = "/ingest"

_ALLOWED_ALGORITHMS: dict[str, tuple[int, Any]] = {
    "sha1": (hashlib.sha1().digest_size, hashlib.sha1),
    "sha256": (hashlib.sha256().digest_size, hashlib.sha256),
}


def _secret_bytes(settings: Settings | None) -> bytes | None:
    if settings is None:
        return None

    secret = getattr(settings.zammad, "webhook_hmac_secret", None)
    if secret is not None:
        value = secret.get_secret_value()
        if value and value.strip():
            return value.encode("utf-8")

    # Backwards-compatible: allow existing shared secret config.
    legacy = getattr(settings.server, "webhook_shared_secret", None)
    if legacy is not None:
        value = legacy.get_secret_value()
        if value and value.strip():
            return value.encode("utf-8")

    return None


def _forbidden():
    return api_error(403, "forbidden", code="forbidden")


def _service_misconfigured():
    # Fail closed: running without webhook auth is almost always a production footgun.
    return api_error(503, "webhook_auth_not_configured", code="webhook_auth_not_configured")


def _missing_delivery_id():
    return api_error(400, "missing_delivery_id", code="missing_delivery_id")


def _parse_signature(value: str) -> tuple[bytes, type] | None:
    """Parse X-Hub-Signature (sha1=<hex> or sha256=<hex>).
    Returns (digest_bytes, digest_constructor) or None."""
    try:
        algorithm, hex_digest = value.strip().split("=", 1)
    except ValueError:
        return None

    algo_lower = algorithm.strip().lower()
    if algo_lower not in _ALLOWED_ALGORITHMS:
        return None

    expected_size, digest_ctor = _ALLOWED_ALGORITHMS[algo_lower]
    hex_digest = hex_digest.strip()
    try:
        digest = bytes.fromhex(hex_digest)
    except ValueError:
        return None

    if len(digest) != expected_size:
        return None

    return (digest, digest_ctor)





async def _read_body(
    receive: Receive, *, on_chunk: Callable[[bytes], None]
) -> tuple[list[bytes], bool]:
    """
    Read body and update MAC. Returns (chunks, disconnected).
    If disconnected is True, client disconnected during read (Bug #28: treat as auth failure).
    """
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
        nonlocal idx
        if idx >= len(chunks):
            return {"type": "http.request", "body": b"", "more_body": False}

        body = chunks[idx]
        idx += 1
        return {"type": "http.request", "body": body, "more_body": idx < len(chunks)}

    return receive


class HmacVerifyMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings | None) -> None:
        self.app = app
        self._secret = _secret_bytes(settings)
        webhook = getattr(getattr(settings, "hardening", None), "webhook", None)
        self._allow_unsigned = (
            bool(getattr(webhook, "allow_unsigned", False)) if settings else False
        )
        self._allow_unsigned_when_no_secret = (
            bool(getattr(webhook, "allow_unsigned_when_no_secret", False))
            if settings
            else False
        )
        self._require_delivery_id = (
            bool(getattr(webhook, "require_delivery_id", False)) if settings else False
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if scope.get("method") != "POST" or scope.get("path") != _INGEST_PATH:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)

        # Require non-empty delivery id (missing or blank header → 400).
        if self._require_delivery_id and not (headers.get(DELIVERY_ID_HEADER) or "").strip():
            await drain_stream(receive)
            await _missing_delivery_id()(scope, receive, send)
            return

        if not self._secret:
            # Bug #12: require explicit allow_unsigned_when_no_secret to allow without secret.
            if self._allow_unsigned and self._allow_unsigned_when_no_secret:
                await self.app(scope, receive, send)
            else:
                await _service_misconfigured()(scope, receive, send)
            return

        signature_raw = headers.get(_SIGNATURE_HEADER)
        if not signature_raw:
            await drain_stream(receive)
            await _forbidden()(scope, receive, send)
            return

        parsed = _parse_signature(signature_raw)
        if parsed is None:
            await drain_stream(receive)
            await _forbidden()(scope, receive, send)
            return

        signature, digest_ctor = parsed
        mac = hmac.new(self._secret, digestmod=digest_ctor)
        chunks, disconnected = await _read_body(receive, on_chunk=mac.update)
        if disconnected:
            await _forbidden()(scope, receive, send)
            return

        expected = mac.digest()
        if not hmac.compare_digest(signature, expected):
            await _forbidden()(scope, receive, send)
            return

        await self.app(scope, _replay_receive(chunks), send)
