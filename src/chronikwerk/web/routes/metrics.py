"""Expose optional Prometheus metrics behind bearer-token protection."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from chronikwerk.configuration.models import Settings
from chronikwerk.web.responses import verify_bearer_token

router = APIRouter()


@router.get("/metrics")
def metrics(request: Request) -> Response:
    """Return Prometheus metrics only when metrics access is configured."""
    settings: Settings = request.app.state.settings
    verify_bearer_token(
        request,
        settings.observability.metrics_bearer_token,
        missing_detail="metrics_auth_not_configured",
    )
    export: Callable[[], None] | None = getattr(
        request.app.state.scheduler, "export_journal_metrics", None
    )
    if export is not None:
        export()
    return Response(content=generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})
