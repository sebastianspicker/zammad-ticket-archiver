"""Strict response policy for all administration paths."""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_HEADERS = (
    (b"cache-control", b"no-store"),
    (
        b"content-security-policy",
        b"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        b"connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; "
        b"form-action 'self'",
    ),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
)


class AdminSecurityHeadersMiddleware:
    """Apply browser hardening headers to all admin responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not str(scope.get("path", "")).startswith("/admin"):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            """Inject missing hardening headers before the response starts."""
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {name.lower() for name, _value in headers}
                headers.extend(item for item in _HEADERS if item[0] not in existing)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
