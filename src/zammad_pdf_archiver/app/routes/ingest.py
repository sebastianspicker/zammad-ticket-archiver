from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, model_validator
from starlette.responses import JSONResponse

from zammad_pdf_archiver.app.constants import DELIVERY_ID_HEADER, REQUEST_ID_KEY
from zammad_pdf_archiver.app.jobs.process_ticket import process_ticket
from zammad_pdf_archiver.app.jobs.shutdown import is_shutting_down, track_task
from zammad_pdf_archiver.app.responses import api_error
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.ticket_id import extract_ticket_id

router = APIRouter()

log = structlog.get_logger(__name__)


class IngestPayload(BaseModel):
    """Minimal webhook payload schema: require resolvable ticket id; allow extra fields."""

    model_config = ConfigDict(extra="allow")

    ticket: dict[str, Any] | None = None
    ticket_id: int | None = None

    @model_validator(mode="after")
    def _require_ticket_id(self) -> IngestPayload:
        tid = self.resolved_ticket_id()
        if tid is None or tid < 1:
            raise ValueError("Payload must contain ticket.id or ticket_id (positive integer)")
        return self

    def resolved_ticket_id(self) -> int | None:
        return extract_ticket_id(self.model_dump())


async def _run_process_ticket_background(
    *,
    delivery_id: str | None,
    payload: dict[str, Any],
    settings: Settings,
) -> None:
    ticket_id = extract_ticket_id(payload)
    if ticket_id is None:
        log.warning("ingest.skip_background_no_ticket_id", delivery_id=delivery_id)
        return

    bound: dict[str, object] = {"ticket_id": ticket_id}
    if delivery_id:
        bound["delivery_id"] = delivery_id

    structlog.contextvars.bind_contextvars(**bound)
    try:
        await process_ticket(delivery_id, payload, settings)
    except Exception:
        log.exception(
            "ingest.process_ticket_unhandled_error",
            ticket_id=ticket_id,
            delivery_id=delivery_id,
        )
    finally:
        structlog.contextvars.unbind_contextvars(*bound.keys())


@router.post("/ingest", status_code=202)
async def ingest_webhook(
    request: Request,
    payload: IngestPayload,
    dry_run: bool = False,
) -> JSONResponse:
    if is_shutting_down():
        return api_error(503, "Service is shutting down", code="shutting_down")
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        return api_error(503, "settings not configured", code="settings_not_configured")

    ticket_id = payload.resolved_ticket_id()
    if dry_run:
        return JSONResponse(
            status_code=202,
            content={"status": "dry_run_accepted", "ticket_id": ticket_id},
        )

    if ticket_id is not None:
        delivery_id_raw = request.headers.get(DELIVERY_ID_HEADER)
        delivery_id = (delivery_id_raw or "").strip() or None
        payload_for_job = payload.model_dump()
        payload_for_job[REQUEST_ID_KEY] = getattr(request.state, "request_id", None)

        task = asyncio.create_task(
            _run_process_ticket_background(
                delivery_id=delivery_id,
                payload=payload_for_job,
                settings=settings,
            )
        )
        track_task(task)

    return JSONResponse(status_code=202, content={"status": "accepted", "ticket_id": ticket_id})


@router.post("/ingest/batch", status_code=202)
async def batch_ingest(
    request: Request,
    payloads: list[IngestPayload],
    dry_run: bool = False,
) -> JSONResponse:
    if is_shutting_down():
        return api_error(503, "Service is shutting down", code="shutting_down")
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        return api_error(503, "settings not configured", code="settings_not_configured")

    if dry_run:
        return JSONResponse(
            status_code=202,
            content={"status": "dry_run_accepted", "count": len(payloads)},
        )

    accepted = 0
    for payload in payloads:
        ticket_id = payload.resolved_ticket_id()
        if ticket_id is not None:
            payload_for_job = payload.model_dump()
            payload_for_job[REQUEST_ID_KEY] = getattr(request.state, "request_id", None)
            task = asyncio.create_task(
                _run_process_ticket_background(
                    delivery_id=None,
                    payload=payload_for_job,
                    settings=settings,
                )
            )
            track_task(task)
            accepted += 1

    return JSONResponse(status_code=202, content={"status": "accepted", "count": accepted})


@router.post("/retry/{ticket_id}", status_code=202)
async def retry_ticket(
    request: Request,
    ticket_id: int,
) -> JSONResponse:
    if is_shutting_down():
        return api_error(503, "Service is shutting down", code="shutting_down")
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        return api_error(503, "settings not configured", code="settings_not_configured")

    payload_for_job: dict[str, Any] = {"ticket_id": ticket_id}
    payload_for_job[REQUEST_ID_KEY] = getattr(request.state, "request_id", None)

    task = asyncio.create_task(
        _run_process_ticket_background(
            delivery_id=None,  # Retry does not need deduplication
            payload=payload_for_job,
            settings=settings,
        )
    )
    track_task(task)

    return JSONResponse(status_code=202, content={"status": "accepted", "ticket_id": ticket_id})
