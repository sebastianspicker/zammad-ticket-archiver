"""Managed configuration revision page and JSON routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from starlette.responses import JSONResponse, RedirectResponse, Response

from chronikwerk.configuration.revisions import (
    ManagedConfigError,
    RevisionConflict,
    validate_candidate,
)
from chronikwerk.web.admin._config_routes import _apply_config_change, _ConfigChangeContext
from chronikwerk.web.admin._route_support import (
    _api_session,
    _html_session,
    _render,
    _request_id,
    _settings,
    _urlencoded,
)
from chronikwerk.web.admin.auth import csrf_token_matches, session_from_request


class RestoreRequest(BaseModel):
    """Accept a revision identifier to restore as the managed configuration."""

    model_config = ConfigDict(extra="forbid")
    security_acknowledged: bool = False


async def revisions_page(
    request: Request,
    restore_error: bool = False,
    acknowledgement_required: bool = False,
) -> Response:
    """Render retained managed-configuration revisions for administrators."""
    session, redirect = _html_session(request)
    if redirect is not None or session is None:
        return redirect or RedirectResponse("/admin/login", status_code=303)
    store = request.app.state.managed_config_store
    return _render(
        "revisions.html",
        request=request,
        session=session,
        current="revisions",
        revisions=store.list_revisions(),
        current_revision=store.current_revision(),
        restore_error=restore_error,
        acknowledgement_required=acknowledgement_required,
    )


async def restore_form(request: Request, revision: str) -> Response:
    """Restore a retained configuration revision after CSRF validation."""
    data = await _urlencoded(request)
    session = session_from_request(request)
    if session is None or not csrf_token_matches(data.get("csrf_token"), session):
        return RedirectResponse("/admin/login", status_code=303)
    if data.get("security_acknowledged") != "true":
        return RedirectResponse(
            "/admin/configuration/revisions?acknowledgement_required=true",
            status_code=303,
        )
    try:
        overlay = request.app.state.managed_config_store.revision_overlay(revision)
        validate_candidate(_settings(request), overlay)
        request.app.state.managed_config_store.restore(
            revision,
            expected_revision=data.get("expected_revision", ""),
            request_id=_request_id(request),
        )
    except ManagedConfigError, OSError, RevisionConflict, ValueError:
        return RedirectResponse(
            "/admin/configuration/revisions?restore_error=true",
            status_code=303,
        )
    return RedirectResponse("/admin/configuration", status_code=303)


async def revisions_api(request: Request) -> Response:
    """Return retained configuration revisions without exposing secrets."""
    session, error = _api_session(request)
    if error is not None or session is None:
        return error or Response(status_code=401)
    store = request.app.state.managed_config_store
    return JSONResponse({"items": store.list_revisions(), "revision": store.current_revision()})


async def restore_api(
    request: Request,
    payload: RestoreRequest,
    revision: str,
) -> Response:
    """Restore a requested configuration revision through the JSON API."""
    session, error = _api_session(request, csrf=True)
    if error is not None or session is None:
        return error or Response(status_code=401)
    expected = request.headers.get("If-Match", "")
    metadata, error = _apply_config_change(
        _ConfigChangeContext(
            request=request,
            session=session,
            overlay=lambda: request.app.state.managed_config_store.revision_overlay(revision),
            acknowledged=payload.security_acknowledged,
            change=lambda _normalized: request.app.state.managed_config_store.restore(
                revision,
                expected_revision=expected,
                request_id=_request_id(request),
            ),
            invalid_code="config_restore_failed",
        ),
    )
    if error is not None:
        return error
    assert metadata is not None
    return JSONResponse({**metadata, "restart_required": True})


def register_revision_page_routes(router: APIRouter) -> None:
    """Register this route group during application startup."""
    router.add_api_route(
        "/configuration/revisions",
        revisions_page,
        methods=["GET"],
    )
    router.add_api_route(
        "/configuration/revisions/{revision}/restore",
        restore_form,
        methods=["POST"],
    )


def register_revision_api_routes(router: APIRouter) -> None:
    """Register this route group during application startup."""
    router.add_api_route(
        "/api/v1/config/revisions",
        revisions_api,
        methods=["GET"],
    )
    router.add_api_route(
        "/api/v1/config/revisions/{revision}/restore",
        restore_api,
        methods=["POST"],
    )
