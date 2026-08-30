"""Shared request, authentication, and rendering helpers for admin routes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from chronikwerk.configuration.models import Settings
from chronikwerk.i18n import normalize_locale, translate
from chronikwerk.web.admin.auth import (
    SESSION_COOKIE,
    AdminSession,
    csrf_matches,
    session_from_request,
)
from chronikwerk.web.admin.templates import render_admin_template


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "") or "")


def _locale(request: Request, session: AdminSession | None = None) -> str:
    if session is not None:
        return session.locale
    return normalize_locale(
        request.query_params.get("lang"),
        default=_settings(request).admin.default_locale,
    )


def _api_error(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    locale: str | None = None,
    **extra: Any,
) -> JSONResponse:
    content: dict[str, Any] = {
        "code": code,
        "message": translate(locale or _locale(request), message),
        "request_id": _request_id(request),
        **extra,
    }
    return JSONResponse(status_code=status_code, content=content)


def _api_session(
    request: Request, *, csrf: bool = False
) -> tuple[AdminSession | None, Response | None]:
    session = session_from_request(request)
    if session is None:
        return None, _api_error(
            request,
            401,
            "admin_session_required",
            "admin.session_expired",
        )
    if csrf and not csrf_matches(request, session):
        return None, _api_error(
            request,
            403,
            "csrf_invalid",
            "admin.session_expired",
            locale=session.locale,
        )
    return session, None


def _html_session(request: Request) -> tuple[AdminSession | None, RedirectResponse | None]:
    session = session_from_request(request)
    if session is None:
        next_path = request.url.path
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        return None, RedirectResponse(
            f"/admin/login?{urlencode({'next': next_path})}",
            status_code=303,
        )
    return session, None


def _safe_next(value: str | None) -> str:
    if not value:
        return "/admin"
    return value if _is_admin_destination(value) else "/admin"


def _is_admin_destination(value: str) -> bool:
    """Accept only local paths within the administration namespace."""
    return value == "/admin" or value.startswith("/admin?") or value.startswith("/admin/")


def _safe_admin_referer(request: Request, value: str | None) -> str:
    if not value:
        return "/admin"
    try:
        target = urlsplit(value)
        base = urlsplit(str(request.base_url))
        target_origin = (target.scheme, target.hostname, target.port)
        base_origin = (base.scheme, base.hostname, base.port)
    except ValueError:
        return "/admin"
    if target_origin != base_origin:
        return "/admin"
    relative = target.path
    if target.query:
        relative = f"{relative}?{target.query}"
    return _safe_next(relative)


def _next_history_cursor(items: list[dict[str, Any]], limit: int) -> int | None:
    if len(items) <= limit:
        return None
    return int(items[limit - 1]["id"])


async def _urlencoded(request: Request) -> dict[str, str]:
    try:
        body = (await request.body()).decode("utf-8")
    except UnicodeDecodeError:
        return {}
    data = parse_qs(body, keep_blank_values=True)
    return {key: values[-1] for key, values in data.items() if values}


def _set_session_cookie(response: Response, request: Request, session: AdminSession) -> None:
    settings = _settings(request).admin
    response.set_cookie(
        SESSION_COOKIE,
        session.session_id,
        max_age=settings.session_absolute_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/admin",
    )


def _display_timestamp(value: float, locale: str) -> str:
    timestamp = datetime.fromtimestamp(value, tz=UTC)
    fmt = "%d.%m.%Y %H:%M:%S UTC" if locale == "de-DE" else "%d/%m/%Y %H:%M:%S UTC"
    return timestamp.strftime(fmt)


def _decorate_history(items: list[dict[str, Any]], locale: str) -> list[dict[str, Any]]:
    return [
        {**item, "created_display": _display_timestamp(float(item["created_at"]), locale)}
        for item in items
    ]


def _render(
    template: str, *, request: Request, session: AdminSession | None, **context: Any
) -> HTMLResponse:
    locale = _locale(request, session)
    html = render_admin_template(
        template,
        locale=locale,
        request=request,
        session=session,
        **context,
    )
    return HTMLResponse(html)


def _schedule_retry(request: Request, *, ticket_id: int, settings: Settings) -> bool:
    """Schedule through the application-owned scheduling service."""
    _ = settings
    scheduler = getattr(request.app.state, "scheduler", None)
    return bool(
        scheduler and scheduler.schedule_retry(ticket_id=ticket_id, request_id=_request_id(request))
    )
