from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_REQUEST_ID_HEADER = "X-Request-Id"
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

CallNext = Callable[[Request], Awaitable[Response]]


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: CallNext) -> Response:
        request_id = (request.headers.get(_REQUEST_ID_HEADER) or "").strip()
        if not _REQUEST_ID_RE.fullmatch(request_id):
            request_id = str(uuid.uuid4())

        request.state.request_id = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id)

        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")

        response.headers[_REQUEST_ID_HEADER] = request_id
        return response
