"""Reject oversized request bodies before they consume unbounded memory."""

from __future__ import annotations

import asyncio

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from chronikwerk.configuration.models import Settings
from chronikwerk.web.constants import INGEST_PROTECTED_PATHS
from chronikwerk.web.responses import api_error

_ADMIN_AUTH_PATHS = frozenset({"/admin/login", "/admin/api/v1/session"})
_ADMIN_AUTH_MAX_BYTES = 16 * 1024
_ADMIN_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_ADMIN_BODY_MAX_BYTES = 256 * 1024
_ABSOLUTE_INGEST_MAX_BYTES = 32 * 1024 * 1024


class _BodyTooLarge(Exception):
    pass


def _too_large():
    response = api_error(413, "request_too_large", code="request_too_large")
    response.headers["Connection"] = "close"
    return response


def _body_timeout():
    response = api_error(408, "request_body_timeout", code="request_body_timeout")
    response.headers["Connection"] = "close"
    return response


def _is_limited_path(scope: Scope, max_bytes: int) -> bool:
    return scope["type"] == "http" and max_bytes > 0 and scope.get("path") in INGEST_PROTECTED_PATHS


def _is_admin_request(scope: Scope, *, admin_enabled: bool) -> bool:
    """Return whether an enabled admin mutation needs the admin body budget."""
    path = str(scope.get("path") or "")
    return (
        scope["type"] == "http"
        and admin_enabled
        and scope.get("method") in _ADMIN_BODY_METHODS
        and (path == "/admin" or path.startswith("/admin/"))
    )


def _request_body_limit(scope: Scope, ingest_max_bytes: int, *, admin_enabled: bool) -> int:
    if scope["type"] != "http":
        return 0
    path = str(scope.get("path") or "")
    if _is_admin_request(scope, admin_enabled=admin_enabled):
        return _ADMIN_AUTH_MAX_BYTES if path in _ADMIN_AUTH_PATHS else _ADMIN_BODY_MAX_BYTES
    if _is_limited_path(scope, ingest_max_bytes):
        return ingest_max_bytes
    return 0


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


def _limited_receive_factory(
    receive: Receive,
    max_bytes: int,
    *,
    timeout_seconds: float = 10.0,
) -> Receive:
    received = 0
    deadline: float | None = None

    async def limited_receive() -> Message:
        """Wrap the ASGI receive callable to enforce the configured byte budget."""
        nonlocal deadline, received
        if deadline is None:
            deadline = asyncio.get_running_loop().time() + timeout_seconds
        async with asyncio.timeout_at(deadline):
            message = await receive()
        if message.get("type") == "http.disconnect":
            return message
        if message.get("type") == "http.request":
            body = message.get("body", b"") or b""
            received += len(body)
            if 0 < max_bytes < received:
                raise _BodyTooLarge()
        return message

    return limited_receive


class BodySizeLimitMiddleware:
    """Enforce body limits on ingest requests and unauthenticated admin login requests.

    Security note on chunked transfer encoding: ASGI servers (uvicorn,
    hypercorn) decode chunked TE before delivering ``http.request``
    messages, so ``_limited_receive_factory`` counts the real decoded
    bytes regardless of the wire encoding.  No additional handling is
    needed.
    """

    def __init__(self, app: ASGIApp, *, settings: Settings | None) -> None:
        self.app = app

        if settings is None:
            self._enabled = False
            self._max_bytes = 0
            self._timeout_seconds = 10.0
            self._admin_enabled = False
            return

        self._enabled = True
        configured_max_bytes = int(settings.hardening.body_size_limit.max_bytes)
        self._max_bytes = (
            min(configured_max_bytes, _ABSOLUTE_INGEST_MAX_BYTES)
            if configured_max_bytes > 0
            else _ABSOLUTE_INGEST_MAX_BYTES
        )
        self._timeout_seconds = float(settings.hardening.body_size_limit.timeout_seconds)
        self._admin_enabled = bool(settings.admin.enabled)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        max_bytes = _request_body_limit(
            scope,
            self._max_bytes,
            admin_enabled=self._admin_enabled,
        )
        limited_path = self._enabled and max_bytes > 0
        if not limited_path:
            await self.app(scope, receive, send)
            return

        if max_bytes > 0 and _content_length_exceeds_limit(scope, max_bytes):
            await _too_large()(scope, receive, send)
            return

        try:
            await self.app(
                scope,
                _limited_receive_factory(
                    receive,
                    max_bytes,
                    timeout_seconds=self._timeout_seconds,
                ),
                send,
            )
        except _BodyTooLarge:
            await _too_large()(scope, receive, send)
        except TimeoutError:
            await _body_timeout()(scope, receive, send)
