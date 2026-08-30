"""Expose protected job-history diagnostics when enabled."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from chronikwerk.operations.history import read_history
from chronikwerk.web.responses import verify_bearer_token

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/history")
def job_history(
    request: Request,
    limit: int = 100,
    ticket_id: int | None = None,
) -> dict[str, object]:
    """Return optional job history only when its diagnostic feature is enabled."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None or not settings.observability.history_enabled:
        raise HTTPException(status_code=404, detail="not_found")
    verify_bearer_token(
        request,
        settings.observability.history_bearer_token,
        missing_detail="history_token_not_configured",
    )
    entries = read_history(limit=limit, ticket_id=ticket_id)
    return {"entries": entries}
