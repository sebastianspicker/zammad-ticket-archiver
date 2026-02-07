from __future__ import annotations

from fastapi import FastAPI

from zammad_pdf_archiver._version import __version__
from zammad_pdf_archiver.app.middleware.body_size_limit import BodySizeLimitMiddleware
from zammad_pdf_archiver.app.middleware.hmac_verify import HmacVerifyMiddleware
from zammad_pdf_archiver.app.middleware.rate_limit import RateLimitMiddleware
from zammad_pdf_archiver.app.middleware.request_id import RequestIdMiddleware
from zammad_pdf_archiver.app.routes.healthz import router as healthz_router
from zammad_pdf_archiver.app.routes.ingest import router as ingest_router
from zammad_pdf_archiver.config.settings import Settings


def _wire_app(app: FastAPI, *, settings: Settings | None) -> None:
    app.state.settings = settings

    app.add_middleware(HmacVerifyMiddleware, settings=settings)
    app.add_middleware(BodySizeLimitMiddleware, settings=settings)
    app.add_middleware(RateLimitMiddleware, settings=settings)
    app.add_middleware(RequestIdMiddleware)

    app.include_router(healthz_router)
    app.include_router(ingest_router)
    if settings is not None and settings.observability.metrics_enabled:
        from zammad_pdf_archiver.app.routes.metrics import router as metrics_router

        app.include_router(metrics_router)


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="zammad-pdf-archiver", version=__version__)
    _wire_app(app, settings=settings)
    return app


app = FastAPI(title="zammad-pdf-archiver", version=__version__)
_wire_app(app, settings=None)
