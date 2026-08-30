"""Expose health without leaking optional service metadata."""

from __future__ import annotations

import asyncio
import tempfile
from datetime import UTC, datetime
from importlib import metadata

import structlog
from fastapi import APIRouter, Request
from starlette.responses import JSONResponse

from chronikwerk.configuration.models import Settings
from chronikwerk.configuration.redaction import scrub_secrets_in_text
from chronikwerk.operations.async_work import run_sync_cancellation_safe
from chronikwerk.web.responses import api_error

router = APIRouter()
log = structlog.get_logger(__name__)


def _service_version() -> str:
    try:
        return metadata.version("chronikwerk")
    except metadata.PackageNotFoundError:
        return "0.0.0"


def _check_storage(settings: Settings) -> dict[str, object]:
    root = settings.storage.root
    try:
        with tempfile.NamedTemporaryFile(dir=root, delete=True):
            return {"writable": True}
    except OSError as exc:
        log.warning("healthz.storage_check_failed", error=scrub_secrets_in_text(str(exc)))
        return {"writable": False, "reason": "storage_unavailable"}


def _deep_check_healthy(_name: str, result: object) -> bool | None:
    if not isinstance(result, dict):
        return None
    if "available" in result:
        return bool(result["available"])
    if "writable" in result:
        return bool(result["writable"])
    return None


async def _deep_checks(settings: Settings) -> tuple[dict[str, object], bool]:
    checks: dict[str, object] = {}
    checks["storage"] = await run_sync_cancellation_safe(_check_storage, settings)
    healthy_checks = [
        result
        for name, value in checks.items()
        if (result := _deep_check_healthy(name, value)) is not None
    ]
    return checks, bool(healthy_checks) and all(healthy_checks)


@router.get("/healthz", response_model=None)
async def healthz(request: Request, deep: bool = False) -> dict[str, object] | JSONResponse:
    """Return service health; include storage check when deep=True."""
    out: dict[str, object] = {
        "status": "ok",
        "time": datetime.now(UTC).isoformat(),
    }
    settings = getattr(request.app.state, "settings", None)
    if settings is None or not settings.observability.healthz_omit_version:
        out["service"] = "chronikwerk"
        out["version"] = _service_version()

    if deep and settings is not None:
        deep_health_lock: asyncio.Lock = request.app.state.deep_health_lock
        if deep_health_lock.locked():
            return api_error(
                503,
                "deep_health_check_busy",
                code="deep_health_check_busy",
                headers={"Retry-After": "1"},
            )
        async with deep_health_lock:
            checks, all_ok = await _deep_checks(settings)
            out["checks"] = checks
            if not all_ok:
                out["status"] = "degraded"

    return out
