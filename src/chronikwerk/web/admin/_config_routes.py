"""Managed configuration page and JSON routes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict
from starlette.responses import JSONResponse, RedirectResponse, Response

from chronikwerk.configuration.models import Settings
from chronikwerk.configuration.revisions import (
    ManagedConfigError,
    RevisionConflict,
    config_read_model,
    environment_owns,
    flatten_mapping,
    get_path,
    overlay_from_flat,
    secret_presence,
    validate_candidate,
    validation_errors,
)
from chronikwerk.web.admin._route_support import (
    _api_error,
    _api_session,
    _html_session,
    _render,
    _request_id,
    _settings,
)
from chronikwerk.web.admin.auth import AdminSession

_ConfigChangeResult = tuple[dict[str, Any], None] | tuple[None, JSONResponse]


class ConfigValidateRequest(BaseModel):
    """Accept a candidate configuration payload without persisting it."""

    model_config = ConfigDict(extra="forbid")
    values: dict[str, Any]
    security_acknowledged: bool = False


class StageRequest(BaseModel):
    """Accept a revision-checked configuration change for staging."""

    model_config = ConfigDict(extra="forbid")
    overlay: dict[str, Any]
    security_acknowledged: bool = False


def _security_change_requires_ack(settings: Settings, values: dict[str, Any]) -> bool:
    current = settings.model_dump(mode="json")
    security_paths = {
        "hardening.transport.trust_env",
        "hardening.transport.allow_insecure_http",
        "hardening.transport.allow_private_networks",
    }
    return any(
        path in values and values[path] != get_path(current, path) for path in security_paths
    )


def _environment_owned_error(
    request: Request,
    session: AdminSession,
    values: dict[str, Any],
) -> JSONResponse | None:
    locked = sorted(path for path in values if environment_owns(path))
    if not locked:
        return None
    return _api_error(
        request,
        422,
        "environment_owned_field",
        "admin.config_intro",
        locale=session.locale,
        errors=[
            {"path": path, "message": "Environment-owned field is read-only"} for path in locked
        ],
    )


def _security_acknowledgement_error(
    request: Request,
    session: AdminSession,
    values: dict[str, Any],
    *,
    acknowledged: bool,
) -> JSONResponse | None:
    if acknowledged or not _security_change_requires_ack(_settings(request), values):
        return None
    return _api_error(
        request,
        422,
        "security_acknowledgement_required",
        "admin.security_ack",
        locale=session.locale,
    )


def _invalid_config_error(
    request: Request,
    session: AdminSession,
    exc: ManagedConfigError | ValueError | OSError,
    *,
    code: str = "config_invalid",
) -> JSONResponse:
    return _api_error(
        request,
        422,
        code,
        "admin.config_intro",
        locale=session.locale,
        errors=validation_errors(exc),
    )


def _normalized_flat_values(settings: Settings, values: dict[str, Any]) -> dict[str, Any]:
    overlay = overlay_from_flat(values)
    _candidate, normalized = validate_candidate(settings, overlay)
    return normalized


def _normalized_overlay(settings: Settings, overlay: dict[str, Any]) -> dict[str, Any]:
    _candidate, normalized = validate_candidate(settings, overlay)
    return normalized


def _config_diff(settings: Settings, normalized: dict[str, Any]) -> list[dict[str, Any]]:
    current = settings.model_dump(mode="json")
    return [
        {"path": path, "before": get_path(current, path), "after": value}
        for path, value in flatten_mapping(normalized).items()
        if get_path(current, path) != value
    ]


@dataclass(frozen=True)
class _ConfigChangeContext:
    """Carry one revision-checked managed configuration change through validation."""

    request: Request
    session: AdminSession
    overlay: dict[str, Any] | Callable[[], dict[str, Any]]
    acknowledged: bool
    change: Callable[[dict[str, Any]], dict[str, Any]]
    preflight: Callable[[], JSONResponse | None] | None = None
    invalid_code: str = "config_invalid"


def _apply_config_change(
    context: _ConfigChangeContext,
) -> _ConfigChangeResult:
    try:
        if context.preflight is not None:
            preflight_error = context.preflight()
            if preflight_error is not None:
                return None, preflight_error
        candidate_overlay = context.overlay() if callable(context.overlay) else context.overlay
        normalized = _normalized_overlay(_settings(context.request), candidate_overlay)
        acknowledgement_error = _security_acknowledgement_error(
            context.request,
            context.session,
            flatten_mapping(normalized),
            acknowledged=context.acknowledged,
        )
        if acknowledgement_error is not None:
            return None, acknowledgement_error
        return context.change(normalized), None
    except RevisionConflict:
        return None, _api_error(
            context.request,
            409,
            "config_revision_conflict",
            "admin.restart_required",
            locale=context.session.locale,
        )
    except (ManagedConfigError, ValueError, OSError) as exc:
        return None, _invalid_config_error(
            context.request,
            context.session,
            exc,
            code=context.invalid_code,
        )


async def configuration_page(request: Request) -> Response:
    """Render the administrative configuration page from the safe read model."""
    session, redirect = _html_session(request)
    if redirect is not None or session is None:
        return redirect or RedirectResponse("/admin/login", status_code=303)
    store = request.app.state.managed_config_store
    current_revision = store.current_revision()
    groups: dict[str, list[dict[str, Any]]] = {}
    for field in config_read_model(_settings(request), store.load()):
        groups.setdefault(str(field["group"]), []).append(field)
    return _render(
        "configuration.html",
        request=request,
        session=session,
        current="configuration",
        field_groups=groups,
        current_revision=current_revision,
        staged_revision=(
            current_revision
            if current_revision != request.app.state.active_config_revision
            else None
        ),
    )


async def config_api(request: Request) -> Response:
    """Return the safe managed-configuration read model for the admin UI."""
    session, error = _api_session(request)
    if error is not None or session is None:
        return error or Response(status_code=401)
    store = request.app.state.managed_config_store
    current = store.current_revision()
    return JSONResponse(
        {
            "fields": config_read_model(_settings(request), store.load()),
            "secret_presence": secret_presence(_settings(request)),
            "active_revision": request.app.state.active_config_revision,
            "staged_revision": (
                current if current != request.app.state.active_config_revision else None
            ),
            "revision": current,
            "restart_required": current != request.app.state.active_config_revision,
        }
    )


async def validate_config_api(request: Request, payload: ConfigValidateRequest) -> Response:
    """Validate a draft configuration without changing the active revision."""
    session, error = _api_session(request, csrf=True)
    if error is not None or session is None:
        return error or Response(status_code=401)
    locked_error = _environment_owned_error(request, session, payload.values)
    if locked_error is not None:
        return locked_error
    acknowledgement_error = _security_acknowledgement_error(
        request,
        session,
        payload.values,
        acknowledged=payload.security_acknowledged,
    )
    if acknowledgement_error is not None:
        return acknowledgement_error
    try:
        normalized = _normalized_flat_values(_settings(request), payload.values)
    except (ManagedConfigError, ValueError) as exc:
        return _invalid_config_error(request, session, exc)
    return JSONResponse(
        {
            "valid": True,
            "overlay": normalized,
            "diff": _config_diff(_settings(request), normalized),
            "revision": request.app.state.managed_config_store.current_revision(),
        }
    )


async def stage_config_api(request: Request, payload: StageRequest) -> Response:
    """Stage a revision-checked configuration change for later activation."""
    session, error = _api_session(request, csrf=True)
    if error is not None or session is None:
        return error or Response(status_code=401)
    expected = request.headers.get("If-Match", "")
    metadata, error = _apply_config_change(
        _ConfigChangeContext(
            request=request,
            session=session,
            overlay=payload.overlay,
            acknowledged=payload.security_acknowledged,
            change=lambda normalized: request.app.state.managed_config_store.stage(
                normalized,
                expected_revision=expected,
                request_id=_request_id(request),
            ),
            preflight=lambda: _environment_owned_error(
                request,
                session,
                flatten_mapping(payload.overlay),
            ),
        )
    )
    if error is not None:
        return error
    assert metadata is not None
    return JSONResponse({**metadata, "restart_required": True})


def register_config_page_routes(router: APIRouter) -> None:
    """Register the HTML configuration page separately from its JSON API routes."""
    router.add_api_route("/configuration", configuration_page, methods=["GET"])


def register_config_api_routes(router: APIRouter) -> None:
    """Register this route group during application startup."""
    router.add_api_route("/api/v1/config", config_api, methods=["GET"])
    router.add_api_route(
        "/api/v1/config/validate",
        validate_config_api,
        methods=["POST"],
    )
    router.add_api_route(
        "/api/v1/config/staged",
        stage_config_api,
        methods=["PUT"],
    )
