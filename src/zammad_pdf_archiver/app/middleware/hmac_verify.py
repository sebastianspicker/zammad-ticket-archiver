from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from zammad_pdf_archiver.config.settings import Settings

_SIGNATURE_HEADER = "X-Hub-Signature"
_EXPECTED_ALGORITHM = "sha1"
_INGEST_PATH = "/ingest"
_DELIVERY_ID_HEADER = "X-Zammad-Delivery"


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


def _forbidden() -> JSONResponse:
    return JSONResponse(status_code=403, content={"detail": "forbidden"})


def _service_misconfigured() -> JSONResponse:
    # Fail closed: running without webhook auth is almost always a production footgun.
    return JSONResponse(status_code=503, content={"detail": "webhook_auth_not_configured"})


def _missing_delivery_id() -> JSONResponse:
    return JSONResponse(status_code=400, content={"detail": "missing_delivery_id"})


def _parse_signature(value: str) -> bytes | None:
    try:
        algorithm, hex_digest = value.strip().split("=", 1)
    except ValueError:
        return None

    if algorithm.lower() != _EXPECTED_ALGORITHM:
        return None

    hex_digest = hex_digest.strip()
    try:
        digest = bytes.fromhex(hex_digest)
    except ValueError:
        return None

    if len(digest) != hashlib.sha1().digest_size:
        return None

    return digest


async def _read_body(receive: Receive, *, on_chunk: Callable[[bytes], None]) -> list[bytes]:
    chunks: list[bytes] = []
    while True:
        message = await receive()
        message_type = message.get("type")
        if message_type == "http.disconnect":
            # Client disconnected before request body completed; abort body read.
            return chunks
        if message_type != "http.request":
            continue

        body = message.get("body", b"")
        if body:
            chunks.append(body)
            on_chunk(body)

        if not message.get("more_body", False):
            return chunks


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
        if self._require_delivery_id and not (headers.get(_DELIVERY_ID_HEADER) or "").strip():
            await _missing_delivery_id()(scope, receive, send)
            return

        if not self._secret:
            if self._allow_unsigned:
                await self.app(scope, receive, send)
            else:
                await _service_misconfigured()(scope, receive, send)
            return

        signature_raw = headers.get(_SIGNATURE_HEADER)
        if not signature_raw:
            await _forbidden()(scope, receive, send)
            return

        signature = _parse_signature(signature_raw)
        if signature is None:
            await _forbidden()(scope, receive, send)
            return

        mac = hmac.new(self._secret, digestmod=hashlib.sha1)
        chunks = await _read_body(receive, on_chunk=mac.update)
        expected = mac.digest()

        if not hmac.compare_digest(signature, expected):
            await _forbidden()(scope, _replay_receive([]), send)
            return

        await self.app(scope, _replay_receive(chunks), send)
