"""Compose the FastAPI web application, routes, middleware, and lifecycle hooks."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request
from starlette.responses import Response

from chronikwerk._version import __version__
from chronikwerk.configuration.models import Settings
from chronikwerk.configuration.revisions import ManagedConfigStore
from chronikwerk.operations.history import EmptyHistory, HistoryReader
from chronikwerk.operations.scheduling import TicketScheduler
from chronikwerk.web.admin.auth import AdminSessionStore
from chronikwerk.web.admin.security import AdminSecurityHeadersMiddleware
from chronikwerk.web.middleware.body_size_limit import BodySizeLimitMiddleware
from chronikwerk.web.middleware.hmac_verify import HmacVerifyMiddleware
from chronikwerk.web.middleware.rate_limit import RateLimitMiddleware
from chronikwerk.web.middleware.request_id import (
    REQUEST_ID_HEADER,
    RequestIdMiddleware,
)
from chronikwerk.web.responses import api_error
from chronikwerk.web.routes.healthz import router as healthz_router
from chronikwerk.web.routes.ingest import router as ingest_router
from chronikwerk.web.routes.jobs import router as jobs_router
from chronikwerk.web.routes.metrics import router as metrics_router


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    """Start the scheduler's workers, drain them on shutdown, then close injected resources."""
    scheduler: TicketScheduler | None = application.state.scheduler
    try:
        start: Callable[[], Awaitable[None]] | None = getattr(scheduler, "start", None)
        if start is not None:
            await start()
        yield
    finally:
        cleanup: Callable[[], Awaitable[None]] | None = application.state.cleanup
        try:
            if scheduler is not None:
                await scheduler.aclose()
        finally:
            if cleanup is not None:
                await cleanup()


async def _global_exception_handler(request: Request, _exc: Exception) -> Response:
    request_id = getattr(request.state, "request_id", None)
    response = api_error(
        500,
        "An internal server error occurred.",
        code="internal_error",
        request_id=request_id,
    )
    if request_id:
        response.headers[REQUEST_ID_HEADER] = request_id
    return response


def _wire_app(
    application: FastAPI,
    *,
    settings: Settings,
    scheduler: TicketScheduler | None,
    history: HistoryReader,
) -> None:
    application.state.settings = settings
    application.state.history = history
    application.state.process_started_at = datetime.now(UTC)
    application.state.deep_health_lock = asyncio.Lock()
    application.state.scheduler = scheduler
    application.add_middleware(HmacVerifyMiddleware, settings=settings)
    application.add_middleware(BodySizeLimitMiddleware, settings=settings)
    application.add_middleware(RateLimitMiddleware, settings=settings)
    application.add_middleware(RequestIdMiddleware)
    application.add_exception_handler(Exception, _global_exception_handler)
    application.include_router(healthz_router)
    application.include_router(ingest_router)
    if settings.observability.history_enabled:
        application.include_router(jobs_router)
    if settings.observability.metrics_enabled:
        application.include_router(metrics_router)
    if settings.admin.enabled:
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
    settings: Settings,
    *,
    scheduler: TicketScheduler | None = None,
    history: HistoryReader | None = None,
    cleanup: Callable[[], Awaitable[None]] | None = None,
) -> FastAPI:
    """Create the FastAPI application with middleware, routes, and lifespan.

    With a scheduler, operator views read that scheduler's history; a scheduler with
    ``start`` is started when the application starts.
    Without one the application is read-only: ingest and retry answer
    ``503 job_capacity_exhausted`` and ``history`` (or an empty one) is displayed.
    """
    if scheduler is not None:
        if history is not None and history is not scheduler.history:
            raise ValueError("history must be the scheduler's history")
        history = scheduler.history
    application = FastAPI(title="chronikwerk", version=__version__, lifespan=lifespan)
    application.state.cleanup = cleanup
    _wire_app(
        application,
        settings=settings,
        scheduler=scheduler,
        history=history if history is not None else EmptyHistory(),
    )
    return application
