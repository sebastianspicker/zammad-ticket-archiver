from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, model_validator

from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.domain.ticket_id import coerce_ticket_id

router = APIRouter()

_DELIVERY_ID_HEADER = "X-Zammad-Delivery"
_REQUEST_ID_KEY = "_request_id"

log = structlog.get_logger(__name__)


class IngestBody(BaseModel):
    """Minimal webhook payload schema: require resolvable ticket id; allow extra fields."""

    model_config = ConfigDict(extra="allow")

    ticket: dict[str, Any] | None = None
    ticket_id: int | None = None

    @model_validator(mode="after")
    def _require_ticket_id(self) -> IngestBody:
        tid = self._resolved_ticket_id()
        if tid is None or tid < 1:
            raise ValueError("Payload must contain ticket.id or ticket_id (positive integer)")
        return self

    def _resolved_ticket_id(self) -> int | None:
        if isinstance(self.ticket, dict):
            return coerce_ticket_id(self.ticket.get("id"))
        return coerce_ticket_id(self.ticket_id)


async def _run_process_ticket_background(
    *,
    delivery_id: str | None,
    payload_for_job: dict[str, Any],
    settings: Any,
    ticket_id: Any,
) -> None:
    # Early validation: ticket_id should not be None at this point
    if ticket_id is None:
        log.warning(
            "ingest.skip_background_no_ticket_id",
            delivery_id=delivery_id,
        )
        return

    bound: dict[str, object] = {"ticket_id": ticket_id}
    if delivery_id:
        bound["delivery_id"] = delivery_id

    structlog.contextvars.bind_contextvars(**bound)
    try:
        await process_ticket(delivery_id, payload_for_job, settings)
    except Exception:
        # Best-effort: never fail the webhook request.
        log.exception(
            "ingest.process_ticket_unhandled_error",
            ticket_id=ticket_id,
            delivery_id=delivery_id,
        )
    finally:
        structlog.contextvars.unbind_contextvars(*bound.keys())


@router.post("/ingest", status_code=202)
async def ingest(
    request: Request, payload: IngestBody, background_tasks: BackgroundTasks
) -> JSONResponse:
    ticket_id = payload._resolved_ticket_id()

    settings = getattr(request.app.state, "settings", None)
    if settings is None and ticket_id is not None:
        log.warning(
            "ingest.settings_not_configured",
            ticket_id=ticket_id,
            message="App settings missing; background processing skipped",
        )
    if settings is not None and ticket_id is not None:
        delivery_id_raw = request.headers.get(_DELIVERY_ID_HEADER)
        # Normalize empty string to None for consistent handling
        delivery_id = delivery_id_raw if (delivery_id_raw and delivery_id_raw.strip()) else None
        payload_for_job = payload.model_dump(exclude_none=False)
        if hasattr(payload, "__pydantic_extra__") and payload.__pydantic_extra__:
            payload_for_job.update(payload.__pydantic_extra__)
        payload_for_job[_REQUEST_ID_KEY] = getattr(request.state, "request_id", None)
        background_tasks.add_task(
            _run_process_ticket_background,
            delivery_id=delivery_id,
            payload_for_job=payload_for_job,
            settings=settings,
            ticket_id=ticket_id,
        )

    return JSONResponse(
        status_code=202,
        content={"accepted": True, "ticket_id": ticket_id},
        background=background_tasks,
    )
