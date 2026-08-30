"""Accept and authenticate Zammad webhook deliveries."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.responses import JSONResponse

from chronikwerk.configuration.models import Settings
from chronikwerk.operations.job import FORCE_REPROCESS_KEY, REQUEST_ID_KEY, extract_ticket_id
from chronikwerk.operations.scheduling import TicketScheduler
from chronikwerk.operations.shutdown import is_shutting_down
from chronikwerk.web.constants import DELIVERY_ID_HEADER
from chronikwerk.web.responses import api_error, settings_or_503, verify_bearer_token

router = APIRouter()

# Security: explicit upper bound on batch size to prevent resource exhaustion.
# The body-size middleware provides some protection, but this is defense-in-depth.
MAX_BATCH_SIZE: int = 100


class IngestPayload(BaseModel):
    """Minimal webhook payload schema: require resolvable ticket id; allow extra fields."""

    model_config = ConfigDict(extra="allow")

    ticket: dict[str, Any] | None = None
    # Security: reject non-positive ticket IDs at the schema level (defense-in-depth).
    ticket_id: int | None = Field(default=None, ge=1)

    @field_validator("ticket_id", mode="before")
    @classmethod
    def _reject_boolean_ticket_id(cls, value: Any) -> Any:
        if isinstance(value, bool):
            raise ValueError("ticket_id must be an integer, not a boolean")
        return value

    @model_validator(mode="after")
    def _require_ticket_id(self) -> IngestPayload:
        tid = self.resolved_ticket_id()
        if tid is None or tid < 1:
            raise ValueError("Payload must contain ticket.id or ticket_id (positive integer)")
        return self

    def resolved_ticket_id(self) -> int | None:
        """Resolve the ticket identifier from the validated webhook event."""
        return extract_ticket_id(self.model_dump())


def _public_payload_for_job(payload: IngestPayload, request_id: str | None) -> dict[str, Any]:
    payload_for_job = payload.model_dump()
    payload_for_job.pop(FORCE_REPROCESS_KEY, None)
    payload_for_job[REQUEST_ID_KEY] = request_id
    return payload_for_job


def _normalized_delivery_id(value: str | None) -> str | None:
    return (value or "").strip() or None


def _batch_jobs(
    payloads: list[IngestPayload],
    *,
    batch_delivery_id: str | None,
    request_id: str | None,
) -> list[tuple[str | None, dict[str, Any]]]:
    jobs: list[tuple[str | None, dict[str, Any]]] = []
    for index, payload in enumerate(payloads):
        if payload.resolved_ticket_id() is None:
            continue
        delivery_id = f"{batch_delivery_id}:{index}" if batch_delivery_id is not None else None
        jobs.append((delivery_id, _public_payload_for_job(payload, request_id)))
    return jobs


def _overload_error() -> JSONResponse:
    response = api_error(
        503,
        "Service is at background job capacity; retry later.",
        code="job_capacity_exhausted",
    )
    response.headers["Retry-After"] = "1"
    return response


def _resolve_settings_or_error(request: Request) -> tuple[Settings | None, JSONResponse | None]:
    if is_shutting_down():
        return None, api_error(503, "Service is shutting down", code="shutting_down")
    settings: Settings | None = getattr(request.app.state, "settings", None)
    if settings is None:
        return None, api_error(503, "settings not configured", code="settings_not_configured")
    return settings, None


def _scheduler(request: Request) -> TicketScheduler | None:
    """Resolve the composition-root-owned scheduling service."""
    return getattr(request.app.state, "scheduler", None)


@router.post("/ingest", status_code=202)
async def ingest_webhook(
    request: Request,
    payload: IngestPayload,
    dry_run: bool = False,
) -> JSONResponse:
    """Accept a single Zammad webhook payload and dispatch it for ticket archival."""
    settings, error = _resolve_settings_or_error(request)
    if error is not None:
        return error
    if settings is None:
        return api_error(503, "settings not configured", code="settings_not_configured")
    scheduler = _scheduler(request)
    if scheduler is None:
        return _overload_error()

    ticket_id = payload.resolved_ticket_id()
    if dry_run:
        return JSONResponse(
            status_code=202,
            content={"status": "dry_run_accepted", "ticket_id": ticket_id},
        )

    if ticket_id is not None:
        delivery_id = _normalized_delivery_id(request.headers.get(DELIVERY_ID_HEADER))
        payload_for_job = _public_payload_for_job(
            payload,
            getattr(request.state, "request_id", None),
        )
        ticket_id = extract_ticket_id(payload_for_job)
        if ticket_id is not None:
            if not scheduler.schedule(
                delivery_id=delivery_id,
                payload=payload_for_job,
            ):
                return _overload_error()

    return JSONResponse(status_code=202, content={"status": "accepted", "ticket_id": ticket_id})


@router.post("/ingest/batch", status_code=202)
async def batch_ingest(
    request: Request,
    payloads: list[IngestPayload],
    dry_run: bool = False,
) -> JSONResponse:
    """Accept a batch of webhook payloads and dispatch each for ticket archival."""
    settings, error = _resolve_settings_or_error(request)
    if error is not None:
        return error
    if settings is None:
        return api_error(503, "settings not configured", code="settings_not_configured")
    scheduler = _scheduler(request)
    if scheduler is None:
        return _overload_error()

    # Security: reject oversized batches before processing any items.
    if len(payloads) > MAX_BATCH_SIZE:
        return api_error(
            422,
            f"batch too large (max {MAX_BATCH_SIZE} items)",
            code="batch_too_large",
        )

    if dry_run:
        return JSONResponse(
            status_code=202,
            content={"status": "dry_run_accepted", "count": len(payloads)},
        )

    jobs = _batch_jobs(
        payloads,
        batch_delivery_id=_normalized_delivery_id(request.headers.get(DELIVERY_ID_HEADER)),
        request_id=getattr(request.state, "request_id", None),
    )
    if not scheduler.schedule_batch(jobs):
        return _overload_error()

    return JSONResponse(status_code=202, content={"status": "accepted", "count": len(jobs)})


@router.post("/retry/{ticket_id}", status_code=202)
async def retry_ticket(
    request: Request,
    # Security: reject non-positive ticket IDs at the parameter level.
    ticket_id: int = Path(..., ge=1),
) -> JSONResponse:
    """Force reprocessing of a ticket by ID, bypassing idempotency checks."""
    settings = settings_or_503(request)
    verify_bearer_token(
        request,
        settings.retry_bearer_token,
        missing_detail="retry_token_not_configured",
    )

    scheduler = _scheduler(request)
    if scheduler is None or not scheduler.schedule_retry(
        ticket_id=ticket_id,
        request_id=getattr(request.state, "request_id", None),
    ):
        return _overload_error()

    return JSONResponse(status_code=202, content={"status": "accepted", "ticket_id": ticket_id})
