"""Centralized API response helpers for consistent JSON error and success shapes."""

from __future__ import annotations

from starlette.responses import JSONResponse


def api_error(
    status_code: int,
    detail: str,
    *,
    code: str | None = None,
    hint: str | None = None,
) -> JSONResponse:
    """Return a JSON error response with optional code and hint."""
    content: dict[str, str] = {"detail": detail}
    if code is not None:
        content["code"] = code
    if hint is not None:
        content["hint"] = hint
    return JSONResponse(status_code=status_code, content=content)
