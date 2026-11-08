"""Accept and authenticate Zammad webhook deliveries."""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from starlette.responses import JSONResponse

from chronikwerk.configuration.models import Settings
from chronikwerk.operations.job import MAX_TICKET_ID, TicketJob, extract_ticket_id
from chronikwerk.operations.scheduling import TicketScheduler
from chronikwerk.web.constants import DELIVERY_ID_HEADER, normalized_delivery_id
from chronikwerk.web.responses import api_error, verify_bearer_token

router = APIRouter()

# Security: explicit upper bound on batch size to prevent resource exhaustion.
# The body-size middleware provides some protection, but this is defense-in-depth.
MAX_BATCH_SIZE: int = 100


class IngestPayload(BaseModel):
    """Minimal webhook payload schema: require resolvable ticket id; allow extra fields."""

    model_config = ConfigDict(extra="allow")

    ticket: dict[str, Any] | None = None
    # Security: reject non-positive ticket IDs at the schema level (defense-in-depth).
    ticket_id: int | None = Field(default=None, ge=1, le=MAX_TICKET_ID)

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

    def ticket_id_value(self) -> int:
        """Return the ticket id that the model validator guarantees is present."""
        return cast(int, self.resolved_ticket_id())


def _login_at(container: Any, key: str) -> str | None:
    value = container.get(key) if isinstance(container, dict) else None
    login = value.get("login") if isinstance(value, dict) else None
    if not isinstance(login, str):
        return None
    login = login.strip()
    return login if login and login != "-" else None


def trigger_login(payload: IngestPayload) -> str | None:
    """Return the login that triggered the webhook: ``user.login``, else ``ticket.updated_by``.

    Zammad sends ``-`` when no user is known; that, blanks and non-strings count as absent.
    """
    body = payload.model_dump()
    return _login_at(body, "user") or _login_at(body.get("ticket"), "updated_by")


def _job(
    *,
    ticket_id: int,
    delivery_id: str | None,
    request_id: str | None,
    trigger_login: str | None = None,
) -> TicketJob:
    """Build a typed job; the journal keeps the ticket, delivery and login, never the body.

    A webhook body therefore cannot request forced reprocessing (such as a legacy
    ``_force_reprocess`` marker): only ``/retry`` can.
    """
    return TicketJob(
        ticket_id=ticket_id,
        delivery_id=delivery_id,
        request_id=request_id,
        trigger_login=trigger_login,
    )


def _batch_jobs(
    payloads: list[IngestPayload],
    *,
    batch_delivery_id: str | None,
    request_id: str | None,
) -> list[TicketJob]:
    jobs: list[TicketJob] = []
    for index, payload in enumerate(payloads):
        ticket_id = payload.ticket_id_value()
        delivery_id = f"{batch_delivery_id}:{index}" if batch_delivery_id is not None else None
        jobs.append(
            _job(
                ticket_id=ticket_id,
                delivery_id=delivery_id,
                request_id=request_id,
                trigger_login=trigger_login(payload),
            )
        )
    return jobs


def _overload_error() -> JSONResponse:
    response = api_error(
        503,
        "Service is at background job capacity; retry later.",
        code="job_capacity_exhausted",
    )
    response.headers["Retry-After"] = "1"
    return response


def _scheduler(request: Request) -> TicketScheduler | None:
    """Resolve the composition-root-owned scheduling service."""
    return request.app.state.scheduler


def _accepting_scheduler_or_error(
    request: Request,
) -> tuple[TicketScheduler | None, JSONResponse | None]:
    """Reject ingest during shutdown and when the application is read-only."""
    scheduler = _scheduler(request)
    if scheduler is not None and not scheduler.accepting:
        return None, api_error(503, "Service is shutting down", code="shutting_down")
    if scheduler is None:
        return None, _overload_error()
    return scheduler, None


@router.post("/ingest", status_code=202)
async def ingest_webhook(
    request: Request,
    payload: IngestPayload,
    dry_run: bool = False,
) -> JSONResponse:
    """Accept a single Zammad webhook payload and dispatch it for ticket archival."""
    scheduler, error = _accepting_scheduler_or_error(request)
    if error is not None or scheduler is None:
        return error or _overload_error()

    ticket_id = payload.ticket_id_value()
    if dry_run:
        return JSONResponse(
            status_code=202,
            content={"status": "dry_run_accepted", "ticket_id": ticket_id},
        )

    job = _job(
        ticket_id=ticket_id,
        delivery_id=normalized_delivery_id(request.headers.get(DELIVERY_ID_HEADER)),
        request_id=getattr(request.state, "request_id", None),
        trigger_login=trigger_login(payload),
    )
    if not await scheduler.schedule(job):
        return _overload_error()

    return JSONResponse(status_code=202, content={"status": "accepted", "ticket_id": ticket_id})


@router.post("/ingest/batch", status_code=202)
async def batch_ingest(
    request: Request,
    payloads: list[IngestPayload],
    dry_run: bool = False,
) -> JSONResponse:
    """Accept a batch of webhook payloads and dispatch each for ticket archival."""
    scheduler, error = _accepting_scheduler_or_error(request)
    if error is not None or scheduler is None:
        return error or _overload_error()

    if not payloads:
        return api_error(422, "batch must not be empty", code="batch_empty")

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
        batch_delivery_id=normalized_delivery_id(request.headers.get(DELIVERY_ID_HEADER)),
        request_id=getattr(request.state, "request_id", None),
    )
    if not await scheduler.schedule_batch(jobs):
        return _overload_error()

    return JSONResponse(status_code=202, content={"status": "accepted", "count": len(jobs)})


@router.post("/retry/{ticket_id}", status_code=202)
async def retry_ticket(
    request: Request,
    # Security: reject non-positive ticket IDs at the parameter level.
    ticket_id: int = Path(..., ge=1, le=MAX_TICKET_ID),
) -> JSONResponse:
    """Force reprocessing of a ticket by ID, bypassing idempotency checks."""
    settings: Settings = request.app.state.settings
    verify_bearer_token(
        request,
        settings.retry_bearer_token,
        missing_detail="retry_token_not_configured",
    )

    scheduler = _scheduler(request)
    if scheduler is None or not await scheduler.schedule_retry(
        ticket_id=ticket_id,
        request_id=getattr(request.state, "request_id", None),
    ):
        return _overload_error()

    return JSONResponse(status_code=202, content={"status": "accepted", "ticket_id": ticket_id})
