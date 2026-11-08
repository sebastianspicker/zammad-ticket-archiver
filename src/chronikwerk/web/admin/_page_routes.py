"""HTML, asset, and form routes for the admin application."""

from __future__ import annotations

from datetime import UTC, datetime
from functools import cache
from importlib import resources
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Path, Query, Request
from starlette.responses import RedirectResponse, Response

from chronikwerk._version import __version__
from chronikwerk.i18n import normalize_locale
from chronikwerk.operations.job import MAX_TICKET_ID
from chronikwerk.web.admin._route_support import (
    _admission_status,
    _decorate_history,
    _display_timestamp,
    _html_session,
    _next_history_cursor,
    _read_history,
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

router = APIRouter(prefix="/admin", include_in_schema=False)


@cache
def _packaged_assets() -> tuple[bytes, bytes, bytes, bytes, bytes]:
    """Read only the five immutable packaged assets once per process."""
    root = resources.files("chronikwerk.web").joinpath("static/admin")
    return (
        root.joinpath("admin.css").read_bytes(),
        root.joinpath("admin.js").read_bytes(),
        root.joinpath("chronikwerk-mark.svg").read_bytes(),
        root.joinpath("atkinson-hyperlegible-next.woff2").read_bytes(),
        root.joinpath("atkinson-hyperlegible-mono.woff2").read_bytes(),
    )


@router.get("/static/admin.css")
async def admin_css() -> Response:
    """Serve the fixed packaged stylesheet used by the administration interface."""
    return Response(_packaged_assets()[0], media_type="text/css; charset=utf-8")


@router.get("/static/admin.js")
async def admin_javascript() -> Response:
    """Serve the fixed packaged script used by the administration interface."""
    return Response(_packaged_assets()[1], media_type="text/javascript; charset=utf-8")


@router.get("/static/chronikwerk-mark.svg")
async def brand_mark() -> Response:
    """Serve the fixed Chronikwerk folio-and-timeline mark."""
    return Response(_packaged_assets()[2], media_type="image/svg+xml")


@router.get("/static/atkinson-hyperlegible-next.woff2")
async def font_sans() -> Response:
    """Serve the fixed packaged Atkinson Hyperlegible Next interface font."""
    return Response(_packaged_assets()[3], media_type="font/woff2")


@router.get("/static/atkinson-hyperlegible-mono.woff2")
async def font_mono() -> Response:
    """Serve the fixed packaged Atkinson Hyperlegible Mono font."""
    return Response(_packaged_assets()[4], media_type="font/woff2")


@router.get("/login")
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


@router.post("/login")
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


@router.post("/logout")
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


@router.post("/locale")
async def change_locale(request: Request) -> Response:
    """Persist the selected admin locale in the current browser session."""
    data = await _urlencoded(request)
    session = session_from_request(request)
    if session is None or not csrf_token_matches(data.get("csrf_token"), session):
        return RedirectResponse("/admin/login", status_code=303)
    session.locale = normalize_locale(data.get("locale"), default=session.locale)
    target = _safe_admin_referer(request, request.headers.get("referer"))
    return RedirectResponse(target, status_code=303)


@router.get("")
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
    failures = _decorate_history(await _read_history(request, 10, statuses={"failed"}), locale)
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
        signing_enabled=_settings(request).signing.enabled,
        timestamp_enabled=(
            _settings(request).signing.enabled and _settings(request).signing.timestamp.enabled
        ),
        admission=_admission_status(request),
        active_revision=active_revision,
        staged_revision=current_revision if current_revision != active_revision else None,
        failures=failures,
    )


@router.get("/jobs")
async def jobs_page(
    request: Request,
    ticket_id: int | None = Query(default=None, ge=1, le=MAX_TICKET_ID),
    status: str | None = None,
    before_id: int | None = Query(default=None, ge=1, le=MAX_TICKET_ID),
) -> Response:
    """Render the operator-facing background-job status page."""
    session, redirect = _html_session(request)
    if redirect is not None or session is None:
        return redirect or RedirectResponse("/admin/login", status_code=303)
    statuses = {status} if status else None
    items = await _read_history(request, 51, ticket_id, before_id=before_id, statuses=statuses)
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


@router.get("/jobs/{ticket_id}")
async def ticket_history_page(
    request: Request,
    ticket_id: int = Path(..., ge=1, le=MAX_TICKET_ID),
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
        items=_decorate_history(await _read_history(request, 100, ticket_id), session.locale),
        accepted=accepted,
        retry_unavailable=retry_unavailable,
        request_id=request_id,
    )


@router.post("/jobs/{ticket_id}/retry")
async def retry_form(
    request: Request, ticket_id: int = Path(..., ge=1, le=MAX_TICKET_ID)
) -> Response:
    """Submit a CSRF-protected operator retry request for one ticket."""
    data = await _urlencoded(request)
    session = session_from_request(request)
    if (
        session is None
        or not csrf_token_matches(data.get("csrf_token"), session)
        or data.get("acknowledge_overwrite") != "true"
    ):
        return RedirectResponse(f"/admin/jobs/{ticket_id}", status_code=303)
    if not await _schedule_retry(request, ticket_id=ticket_id):
        return RedirectResponse(
            f"/admin/jobs/{ticket_id}?retry_unavailable=true",
            status_code=303,
        )
    return RedirectResponse(
        f"/admin/jobs/{ticket_id}?accepted=true&request_id={_request_id(request)}",
        status_code=303,
    )
