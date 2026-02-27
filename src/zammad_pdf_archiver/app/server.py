from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from zammad_pdf_archiver._version import __version__
from zammad_pdf_archiver.app.jobs.redis_queue import (
    aclose_queue_clients,
    start_queue_worker,
    stop_queue_worker,
)
from zammad_pdf_archiver.app.jobs.shutdown import (
    clear_shutting_down,
    set_shutting_down,
    track_task,
    wait_for_tasks,
)
from zammad_pdf_archiver.app.jobs.ticket_stores import aclose_stores
from zammad_pdf_archiver.app.middleware.body_size_limit import BodySizeLimitMiddleware
from zammad_pdf_archiver.app.middleware.hmac_verify import HmacVerifyMiddleware
from zammad_pdf_archiver.app.middleware.rate_limit import RateLimitMiddleware
from zammad_pdf_archiver.app.middleware.request_id import RequestIdMiddleware
from zammad_pdf_archiver.app.responses import api_error
from zammad_pdf_archiver.app.routes.healthz import router as healthz_router
from zammad_pdf_archiver.app.routes.ingest import router as ingest_router
from zammad_pdf_archiver.app.routes.jobs import router as jobs_router
from zammad_pdf_archiver.config.settings import Settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    clear_shutting_down()
    settings = getattr(app.state, "settings", None)
    if settings is not None:
        worker = await start_queue_worker(settings)
        if worker is not None:
            track_task(worker)
    yield
    set_shutting_down()
    if settings is not None:
        await stop_queue_worker(settings)
    await wait_for_tasks()
    await aclose_stores()
    await aclose_queue_clients()


async def _global_exception_handler(request, exc):
    from zammad_pdf_archiver.app.middleware.request_id import _REQUEST_ID_HEADER

    request_id = getattr(request.state, "request_id", None)
    headers = {_REQUEST_ID_HEADER: request_id} if request_id else None
    return api_error(
        500,
        "An internal server error occurred.",
        code="internal_error",
        request_id=request_id,
        headers=headers,
    )

def _wire_app(app: FastAPI, *, settings: Settings | None) -> None:
    app.state.settings = settings

    app.add_middleware(HmacVerifyMiddleware, settings=settings)
    app.add_middleware(BodySizeLimitMiddleware, settings=settings)
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(RequestIdMiddleware)
    app.add_exception_handler(Exception, _global_exception_handler)

    app.include_router(healthz_router)
    app.include_router(ingest_router)
    app.include_router(jobs_router)
    if settings is not None and settings.admin.enabled:
        from zammad_pdf_archiver.app.routes.admin import router as admin_router

        app.include_router(admin_router)
    if settings is not None and settings.observability.metrics_enabled:
        from zammad_pdf_archiver.app.routes.metrics import router as metrics_router

        app.include_router(metrics_router)


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="zammad-pdf-archiver", version=__version__, lifespan=lifespan)
    _wire_app(app, settings=settings)
    return app


app = create_app()
