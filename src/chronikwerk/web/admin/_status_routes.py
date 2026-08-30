"""Session, service-status, and job-status JSON routes."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse, Response

from chronikwerk._version import __version__
from chronikwerk.i18n import normalize_locale
from chronikwerk.operations.history import read_history
from chronikwerk.web.admin._route_support import (
    _api_error,
    _api_session,
    _next_history_cursor,
    _request_id,
    _schedule_retry,
    _set_session_cookie,
    _settings,
)
from chronikwerk.web.admin.auth import SESSION_COOKIE, access_token_matches
from chronikwerk.web.routes.healthz import _check_storage


class SessionRequest(BaseModel):
    """Accept administrator credentials used to create a browser session."""

    model_config = ConfigDict(extra="forbid")
    access_token: str = Field(min_length=1, max_length=4096)
    locale: str | None = None


class RetryRequest(BaseModel):
    """Accept the ticket identifier requested for an administrative retry."""

    model_config = ConfigDict(extra="forbid")
    acknowledge_overwrite: bool


async def create_session(request: Request, payload: SessionRequest) -> Response:
    """Create a short-lived admin session after credential verification."""
    settings = _settings(request)
    if not access_token_matches(payload.access_token, settings.admin.access_token):
        return _api_error(
            request,
            401,
            "invalid_credentials",
            "admin.invalid_credentials",
            locale=normalize_locale(payload.locale, default=settings.admin.default_locale),
        )
    request.app.state.admin_sessions.delete(request.cookies.get(SESSION_COOKIE))
    session = request.app.state.admin_sessions.create(
        locale=normalize_locale(payload.locale, default=settings.admin.default_locale)
    )
    response = Response(status_code=204)
    _set_session_cookie(response, request, session)
    return response


async def delete_session(request: Request) -> Response:
    """Invalidate the current admin session and clear its browser cookie."""
    session, error = _api_session(request, csrf=True)
    if error is not None or session is None:
        return error or Response(status_code=401)
    request.app.state.admin_sessions.delete(session.session_id)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/admin")
    return response


async def status_api(request: Request) -> Response:
    """Return the protected operational status view for the admin UI."""
    session, error = _api_session(request)
    if error is not None or session is None:
        return error or Response(status_code=401)
    admission = request.app.state.admission
    store = request.app.state.managed_config_store
    current = store.current_revision()
    return JSONResponse(
        {
            "service": "chronikwerk",
            "version": __version__,
            "process_started_at": request.app.state.process_started_at.isoformat(),
            "health": {"status": "ok"},
            "admission": {
                "pending": admission.pending,
                "running": admission.running,
                "max_pending": admission.max_pending,
                "max_running": admission.max_running,
                "closing": admission.closing,
            },
            "history": {"volatile": True, "limit": 5000},
            "config": {
                "active_revision": request.app.state.active_config_revision,
                "staged_revision": (
                    current if current != request.app.state.active_config_revision else None
                ),
            },
        }
    )


async def storage_check_api(request: Request) -> Response:
    """Check configured storage and return a safe diagnostics result."""
    session, error = _api_session(request, csrf=True)
    if error is not None or session is None:
        return error or Response(status_code=401)
    result = await asyncio.to_thread(_check_storage, _settings(request))
    return JSONResponse({"storage": result, "request_id": _request_id(request)})


async def jobs_api(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    before_id: int | None = Query(default=None, ge=1),
    ticket_id: int | None = Query(default=None, ge=1),
    status: Annotated[list[str] | None, Query()] = None,
) -> Response:
    """Return the protected background-job status view for the admin UI."""
    session, error = _api_session(request)
    if error is not None or session is None:
        return error or Response(status_code=401)
    items = read_history(
        limit + 1,
        ticket_id,
        before_id=before_id,
        statuses=set(status or []),
    )
    next_cursor = _next_history_cursor(items, limit)
    return JSONResponse(
        {
            "items": items[:limit],
            "next_cursor": next_cursor,
            "process_started_at": request.app.state.process_started_at.isoformat(),
            "volatile": True,
        }
    )


async def retry_api(
    request: Request,
    payload: RetryRequest,
    ticket_id: int = Path(..., ge=1),
) -> Response:
    """Schedule an authorized retry without bypassing job admission limits."""
    session, error = _api_session(request, csrf=True)
    if error is not None or session is None:
        return error or Response(status_code=401)
    if not payload.acknowledge_overwrite:
        return _api_error(
            request,
            422,
            "overwrite_acknowledgement_required",
            "admin.retry_warning",
            locale=session.locale,
        )
    if not _schedule_retry(request, ticket_id=ticket_id, settings=_settings(request)):
        response = _api_error(
            request,
            503,
            "job_capacity_exhausted",
            "admin.retry_warning",
            locale=session.locale,
        )
        response.headers["Retry-After"] = "1"
        return response
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "ticket_id": ticket_id,
            "request_id": _request_id(request),
        },
    )


def register_status_routes(router: APIRouter) -> None:
    """Register this route group during application startup."""
    router.add_api_route("/api/v1/session", create_session, methods=["POST"])
    router.add_api_route("/api/v1/session", delete_session, methods=["DELETE"])
    router.add_api_route("/api/v1/status", status_api, methods=["GET"])
    router.add_api_route(
        "/api/v1/status/storage-check",
        storage_check_api,
        methods=["POST"],
    )
    router.add_api_route("/api/v1/jobs", jobs_api, methods=["GET"])
    router.add_api_route(
        "/api/v1/jobs/{ticket_id}/retry",
        retry_api,
        methods=["POST"],
    )
