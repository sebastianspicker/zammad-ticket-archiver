"""Compose the FastAPI web application, routes, middleware, and lifecycle hooks."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from starlette.responses import Response

from chronikwerk._version import __version__
from chronikwerk.configuration.models import Settings
from chronikwerk.configuration.revisions import ManagedConfigStore
from chronikwerk.operations.admission import JobAdmission
from chronikwerk.operations.scheduling import TicketScheduler
from chronikwerk.operations.shutdown import (
    clear_shutting_down,
    set_shutting_down,
    wait_for_tasks,
)
from chronikwerk.operations.ticket_stores import aclose_stores
from chronikwerk.web.admin.auth import AdminSessionStore
from chronikwerk.web.admin.security import AdminSecurityHeadersMiddleware
from chronikwerk.web.middleware.body_size_limit import BodySizeLimitMiddleware
from chronikwerk.web.middleware.hmac_verify import HmacVerifyMiddleware
from chronikwerk.web.middleware.rate_limit import RateLimitMiddleware
from chronikwerk.web.middleware.request_id import (
    _REQUEST_ID_HEADER,
    RequestIdMiddleware,
)
from chronikwerk.web.responses import api_error
from chronikwerk.web.routes.healthz import router as healthz_router
from chronikwerk.web.routes.ingest import router as ingest_router
from chronikwerk.web.routes.jobs import router as jobs_router
from chronikwerk.web.routes.metrics import router as metrics_router


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Track graceful shutdown for in-process jobs."""
    clear_shutting_down()
    try:
        yield
    finally:
        set_shutting_down()
        admission = getattr(application.state, "admission", None)
        if admission is not None:
            await admission.close()
        settings = getattr(application.state, "settings", None)
        timeout = settings.admission.shutdown_timeout_seconds if settings is not None else 1.0
        await wait_for_tasks(timeout=timeout)
        await aclose_stores()


async def _global_exception_handler(request: Request, _exc: Exception) -> Response:
    request_id = getattr(request.state, "request_id", None)
    response = api_error(
        500,
        "An internal server error occurred.",
        code="internal_error",
        request_id=request_id,
    )
    if request_id:
        response.headers[_REQUEST_ID_HEADER] = request_id
    return response


def _wire_app(
    application: FastAPI,
    *,
    settings: Settings | None,
    admission: JobAdmission | None,
    scheduler: TicketScheduler | None,
) -> None:
    application.state.settings = settings
    application.state.process_started_at = datetime.now(UTC)
    application.state.deep_health_lock = asyncio.Lock()
    application.state.admission = admission or (
        JobAdmission(
            max_pending=settings.admission.max_pending,
            max_running=settings.admission.max_running,
        )
        if settings is not None
        else None
    )
    application.state.scheduler = scheduler
    application.add_middleware(HmacVerifyMiddleware, settings=settings)
    application.add_middleware(BodySizeLimitMiddleware, settings=settings)
    application.add_middleware(RateLimitMiddleware, settings=settings)
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(Exception, _global_exception_handler)
    application.include_router(healthz_router)
    application.include_router(ingest_router)
    if settings is not None and settings.observability.history_enabled:
        application.include_router(jobs_router)
    if settings is not None and settings.observability.metrics_enabled:
        application.include_router(metrics_router)
    if settings is not None and settings.admin.enabled:
        from chronikwerk.web.admin.routes import router as admin_router

        store = ManagedConfigStore(settings.admin.state_dir)
        application.state.managed_config_store = store
        application.state.active_config_revision = store.current_revision()
        application.state.admin_sessions = AdminSessionStore(
            idle_seconds=settings.admin.session_idle_seconds,
            absolute_seconds=settings.admin.session_absolute_seconds,
        )
        application.add_middleware(AdminSecurityHeadersMiddleware)
        application.include_router(admin_router)


def create_app(
    settings: Settings | None = None,
    *,
    admission: JobAdmission | None = None,
    scheduler: TicketScheduler | None = None,
) -> FastAPI:
    """Create the FastAPI application with middleware, routes, and lifespan."""
    application = FastAPI(title="chronikwerk", version=__version__, lifespan=lifespan)
    _wire_app(application, settings=settings, admission=admission, scheduler=scheduler)
    return application
