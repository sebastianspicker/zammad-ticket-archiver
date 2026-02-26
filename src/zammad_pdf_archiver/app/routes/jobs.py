from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request

from zammad_pdf_archiver.app.jobs.history import read_history
from zammad_pdf_archiver.app.jobs.redis_queue import drain_dlq, get_queue_stats
from zammad_pdf_archiver.app.jobs.shutdown import is_shutting_down
from zammad_pdf_archiver.app.jobs.ticket_stores import is_ticket_in_flight
from zammad_pdf_archiver.config.settings import Settings

router = APIRouter()


def _settings_or_503(request: Request) -> Settings:
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="settings_not_configured")
    return settings


def _verify_ops_bearer(request: Request, settings: Settings) -> None:
    token = settings.admin.bearer_token
    expected = token.get_secret_value().encode("utf-8") if token is not None else b""
    if not expected:
        raise HTTPException(status_code=503, detail="ops_token_not_configured")

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or len(auth) < 8:
        raise HTTPException(status_code=401, detail="unauthorized")

    provided = auth[7:].strip().encode("utf-8")
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="unauthorized")


@router.get("/jobs/queue/stats")
async def get_queue_status(request: Request) -> dict[str, bool | int | str]:
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        return {"execution_backend": "unknown", "queue_enabled": False}
    try:
        stats = await get_queue_stats(settings)
    except Exception:
        return {
            "execution_backend": (settings.workflow.execution_backend or "inprocess"),
            "queue_enabled": False,
            "status": "error",
            "detail": "queue_unavailable",
        }
    return {str(k): v for k, v in stats.items()}


@router.get("/jobs/history")
async def get_job_history(
    request: Request,
    limit: int = 100,
    ticket_id: int | None = None,
) -> dict[str, int | str | list[dict[str, object]]]:
    settings = _settings_or_503(request)
    _verify_ops_bearer(request, settings)

    bounded_limit = max(1, min(int(limit), 5000))
    try:
        items = await read_history(
            settings,
            limit=bounded_limit,
            ticket_id=ticket_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="history_unavailable") from exc
    return {"status": "ok", "count": len(items), "items": items}


@router.post("/jobs/queue/dlq/drain")
async def drain_queue_dlq(request: Request, limit: int = 100) -> dict[str, int | str]:
    settings = _settings_or_503(request)
    _verify_ops_bearer(request, settings)
    bounded_limit = max(1, min(int(limit), 1000))
    try:
        drained = await drain_dlq(settings, limit=bounded_limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="dlq_unavailable") from exc
    return {"status": "ok", "drained": drained}


@router.get("/jobs/{ticket_id}")
async def get_job_status(ticket_id: int) -> dict[str, bool | int]:
    return {
        "ticket_id": ticket_id,
        "in_flight": is_ticket_in_flight(ticket_id),
        "shutting_down": is_shutting_down(),
    }
