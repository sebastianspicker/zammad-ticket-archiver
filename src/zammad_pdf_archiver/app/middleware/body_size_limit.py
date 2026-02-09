from __future__ import annotations

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from zammad_pdf_archiver.config.settings import Settings

_INGEST_PATH = "/ingest"


class _BodyTooLarge(Exception):
    pass


def _too_large() -> JSONResponse:
    return JSONResponse(status_code=413, content={"detail": "request_too_large"})


async def _drain_body(receive: Receive) -> None:
    """Consume the request body so the connection is left in a clean state."""
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            return
        if message.get("type") == "http.request" and not message.get("more_body", False):
            return


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings | None) -> None:
        self.app = app

        if settings is None:
            self._max_bytes = 0
            return

        self._max_bytes = int(settings.hardening.body_size_limit.max_bytes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if self._max_bytes <= 0 or scope.get("path") != _INGEST_PATH:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self._max_bytes:
                    await _drain_body(receive)
                    await _too_large()(scope, receive, send)
                    return
            except ValueError:
                # Invalid/missing Content-Length will be enforced by streaming size checks.
                pass

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message.get("type") == "http.disconnect":
                return message
            if message.get("type") == "http.request":
                body = message.get("body", b"") or b""
                received += len(body)
                if received > self._max_bytes:
                    raise _BodyTooLarge()
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            await _drain_body(receive)
            await _too_large()(scope, receive, send)

