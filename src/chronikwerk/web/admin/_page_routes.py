"""HTML, asset, and form routes for the admin application."""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import resources
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Path, Query, Request
from starlette.responses import RedirectResponse, Response

from chronikwerk._version import __version__
from chronikwerk.i18n import normalize_locale
from chronikwerk.operations.history import read_history
from chronikwerk.web.admin._route_support import (
    _decorate_history,
    _display_timestamp,
    _html_session,
    _next_history_cursor,
    _render,
    _request_id,
    _safe_admin_referer,
    _safe_next,
    _schedule_retry,
    _set_session_cookie,
    _settings,
    _urlencoded,
)
from chronikwerk.web.admin.auth import (
    SESSION_COOKIE,
    access_token_matches,
    csrf_token_matches,
    session_from_request,
)

_STATUS_OPTIONS = ("accepted", "running", "processed", "failed", "skipped")


async def admin_css() -> Response:
    """Serve the fixed packaged stylesheet used by the administration interface."""
    data = resources.files("chronikwerk.web").joinpath("static/admin/admin.css").read_bytes()
    return Response(data, media_type="text/css; charset=utf-8")


async def admin_javascript() -> Response:
    """Serve the fixed packaged script used by the administration interface."""
    data = resources.files("chronikwerk.web").joinpath("static/admin/admin.js").read_bytes()
    return Response(data, media_type="text/javascript; charset=utf-8")


async def brand_mark() -> Response:
    """Serve the fixed Chronikwerk folio-and-timeline mark."""
    data = (
        resources.files("chronikwerk.web")
        .joinpath("static/admin/chronikwerk-mark.svg")
        .read_bytes()
    )
    return Response(data, media_type="image/svg+xml")


async def login_page(
    request: Request,
    next_path: Annotated[str | None, Query(alias="next")] = None,
    error: bool = False,
) -> Response:
    """Render the administrator login form."""
    if session_from_request(request) is not None:
        return RedirectResponse(_safe_next(next_path), status_code=303)
    return _render(
        "login.html",
        request=request,
        session=None,
        next_path=_safe_next(next_path),
        error=error,
        current="login",
    )


async def login_form(request: Request) -> Response:
    """Authenticate an administrator and establish a protected session."""
    data = await _urlencoded(request)
    settings = _settings(request)
    locale = normalize_locale(data.get("locale"), default=settings.admin.default_locale)
    if not access_token_matches(data.get("access_token", ""), settings.admin.access_token):
        return RedirectResponse(
            "/admin/login?"
            + urlencode(
                {
                    "error": "true",
                    "lang": locale,
                    "next": _safe_next(data.get("next")),
                }
            ),
            status_code=303,
        )
    request.app.state.admin_sessions.delete(request.cookies.get(SESSION_COOKIE))
    session = request.app.state.admin_sessions.create(locale=locale)
    response = RedirectResponse(_safe_next(data.get("next")), status_code=303)
    _set_session_cookie(response, request, session)
    return response


async def logout_form(request: Request) -> Response:
    """End the current administrator session after CSRF validation."""
    data = await _urlencoded(request)
    session = session_from_request(request)
    if session is None or not csrf_token_matches(data.get("csrf_token"), session):
        return RedirectResponse("/admin/login", status_code=303)
    request.app.state.admin_sessions.delete(session.session_id)
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/admin")
    return response


async def change_locale(request: Request) -> Response:
    """Persist the selected admin locale in the current browser session."""
    data = await _urlencoded(request)
    session = session_from_request(request)
    if session is None or not csrf_token_matches(data.get("csrf_token"), session):
        return RedirectResponse("/admin/login", status_code=303)
    session.locale = normalize_locale(data.get("locale"), default=session.locale)
    target = _safe_admin_referer(request, request.headers.get("referer"))
    return RedirectResponse(target, status_code=303)


async def overview_page(request: Request) -> Response:
    """Render the administrative overview with current safe status data."""
    session, redirect = _html_session(request)
    if redirect is not None or session is None:
        return redirect or RedirectResponse("/admin/login", status_code=303)
    locale = session.locale
    now = datetime.now(UTC)
    started: datetime = request.app.state.process_started_at
    store = request.app.state.managed_config_store
    current_revision = store.current_revision()
    active_revision = request.app.state.active_config_revision
    failures = _decorate_history(read_history(10, statuses={"failed"}), locale)
    return _render(
        "overview.html",
        request=request,
        session=session,
        current="overview",
        now_iso=now.isoformat(),
        now_display=_display_timestamp(now.timestamp(), locale),
        process_started_iso=started.isoformat(),
        process_started_display=_display_timestamp(started.timestamp(), locale),
        version=__version__,
        admission=request.app.state.admission,
        active_revision=active_revision,
        staged_revision=current_revision if current_revision != active_revision else None,
        failures=failures,
    )


async def jobs_page(
    request: Request,
    ticket_id: int | None = Query(default=None, ge=1),
    status: str | None = None,
    before_id: int | None = Query(default=None, ge=1),
) -> Response:
    """Render the operator-facing background-job status page."""
    session, redirect = _html_session(request)
    if redirect is not None or session is None:
        return redirect or RedirectResponse("/admin/login", status_code=303)
    statuses = {status} if status else None
    items = read_history(51, ticket_id, before_id=before_id, statuses=statuses)
    next_cursor = _next_history_cursor(items, 50)
    return _render(
        "jobs.html",
        request=request,
        session=session,
        current="jobs",
        items=_decorate_history(items[:50], session.locale),
        next_cursor=next_cursor,
        ticket_id=ticket_id,
        status=status,
        status_options=_STATUS_OPTIONS,
    )


async def ticket_history_page(
    request: Request,
    ticket_id: int = Path(..., ge=1),
    accepted: bool = False,
    retry_unavailable: bool = False,
    request_id: str | None = None,
) -> Response:
    """Render the optional bounded ticket-history diagnostic page."""
    session, redirect = _html_session(request)
    if redirect is not None or session is None:
        return redirect or RedirectResponse("/admin/login", status_code=303)
    return _render(
        "job_detail.html",
        request=request,
        session=session,
        current="jobs",
        ticket_id=ticket_id,
        items=_decorate_history(read_history(100, ticket_id), session.locale),
        accepted=accepted,
        retry_unavailable=retry_unavailable,
        request_id=request_id,
    )


async def retry_form(request: Request, ticket_id: int = Path(..., ge=1)) -> Response:
    """Submit a CSRF-protected operator retry request for one ticket."""
    data = await _urlencoded(request)
    session = session_from_request(request)
    if (
        session is None
        or not csrf_token_matches(data.get("csrf_token"), session)
        or data.get("acknowledge_overwrite") != "true"
    ):
        return RedirectResponse(f"/admin/jobs/{ticket_id}", status_code=303)
    if not _schedule_retry(request, ticket_id=ticket_id, settings=_settings(request)):
        return RedirectResponse(
            f"/admin/jobs/{ticket_id}?retry_unavailable=true",
            status_code=303,
        )
    return RedirectResponse(
        f"/admin/jobs/{ticket_id}?accepted=true&request_id={_request_id(request)}",
        status_code=303,
    )


def register_page_routes(router: APIRouter) -> None:
    """Register this route group during application startup."""
    router.add_api_route("/static/admin.css", admin_css, methods=["GET"])
    router.add_api_route("/static/admin.js", admin_javascript, methods=["GET"])
    router.add_api_route("/static/chronikwerk-mark.svg", brand_mark, methods=["GET"])
    router.add_api_route("/login", login_page, methods=["GET"])
    router.add_api_route("/login", login_form, methods=["POST"])
    router.add_api_route("/logout", logout_form, methods=["POST"])
    router.add_api_route("/locale", change_locale, methods=["POST"])
    router.add_api_route("", overview_page, methods=["GET"])
    router.add_api_route("/jobs", jobs_page, methods=["GET"])
    router.add_api_route("/jobs/{ticket_id}", ticket_history_page, methods=["GET"])
    router.add_api_route("/jobs/{ticket_id}/retry", retry_form, methods=["POST"])
