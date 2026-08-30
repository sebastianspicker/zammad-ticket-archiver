"""Expose optional Prometheus metrics behind bearer-token protection."""

from __future__ import annotations

from fastapi import APIRouter, Request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.responses import Response

from chronikwerk.web.responses import settings_or_503, verify_bearer_token

router = APIRouter()


@router.get("/metrics")
def metrics(request: Request) -> Response:
    """Return Prometheus metrics only when metrics access is configured."""
    settings = settings_or_503(request)
    verify_bearer_token(
        request,
        settings.observability.metrics_bearer_token,
        missing_detail="metrics_auth_not_configured",
    )
    return Response(content=generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})
