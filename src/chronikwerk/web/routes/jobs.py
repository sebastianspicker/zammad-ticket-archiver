"""Expose protected job-history diagnostics when enabled."""

from __future__ import annotations

from fastapi import APIRouter, Request

from chronikwerk.configuration.models import Settings
from chronikwerk.operations.history import HistoryReader
from chronikwerk.web.responses import verify_bearer_token

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/history")
def job_history(
    request: Request,
    limit: int = 100,
    ticket_id: int | None = None,
) -> dict[str, object]:
    """Return job history; the route is only mounted when the feature is enabled."""
    settings: Settings = request.app.state.settings
    verify_bearer_token(
        request,
        settings.observability.history_bearer_token,
        missing_detail="history_token_not_configured",
    )
    history: HistoryReader = request.app.state.history
    entries = history.read(limit=limit, ticket_id=ticket_id)
    return {"entries": entries}
