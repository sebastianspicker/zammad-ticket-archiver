from __future__ import annotations

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from zammad_pdf_archiver.adapters.http_util import drain_stream
from zammad_pdf_archiver.app.constants import INGEST_PROTECTED_PATHS
from zammad_pdf_archiver.app.responses import api_error
from zammad_pdf_archiver.config.settings import Settings


class _BodyTooLarge(Exception):
    pass


def _too_large():
    return api_error(413, "request_too_large", code="request_too_large")


def _is_limited_path(scope: Scope, max_bytes: int) -> bool:
    return (
        scope["type"] == "http"
        and max_bytes > 0
        and scope.get("path") in INGEST_PROTECTED_PATHS
    )


def _content_length_exceeds_limit(scope: Scope, max_bytes: int) -> bool:
    headers = Headers(scope=scope)
    content_length = headers.get("content-length")
    if not content_length:
        return False
    try:
        return int(content_length) > max_bytes
    except ValueError:
        # Invalid/missing Content-Length will be enforced by streaming size checks.
        return False


def _limited_receive_factory(receive: Receive, max_bytes: int) -> Receive:
    received = 0

    async def limited_receive() -> Message:
        nonlocal received
        message = await receive()
        if message.get("type") == "http.disconnect":
            return message
        if message.get("type") == "http.request":
            body = message.get("body", b"") or b""
            received += len(body)
            if received > max_bytes:
                raise _BodyTooLarge()
        return message

    return limited_receive


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings | None) -> None:
        self.app = app

        if settings is None:
            self._max_bytes = 0
            return

        self._max_bytes = int(settings.hardening.body_size_limit.max_bytes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not _is_limited_path(scope, self._max_bytes):
            await self.app(scope, receive, send)
            return

        if _content_length_exceeds_limit(scope, self._max_bytes):
            await drain_stream(receive)
            await _too_large()(scope, receive, send)
            return

        try:
            await self.app(scope, _limited_receive_factory(receive, self._max_bytes), send)
        except _BodyTooLarge:
            await drain_stream(receive)
            await _too_large()(scope, receive, send)
