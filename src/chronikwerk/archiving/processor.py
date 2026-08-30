"""Coordinate one ticket archive from fetch through durable storage."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any

import structlog

from chronikwerk.archiving.options import ArchiveRuntimeOptions
from chronikwerk.archiving.workflow import (
    ArchiveAttempt,
    ArchiveOutcome,
    ArchivePipelineRequest,
    cleanup_cancelled_pipeline,
    record_history,
    run_ticket_pipeline,
)
from chronikwerk.archiving.workflow_errors import (
    handle_ticket_pipeline_exception,
)
from chronikwerk.operations.job import FORCE_REPROCESS_KEY, REQUEST_ID_KEY, extract_ticket_id
from chronikwerk.operations.metrics import (
    skipped_total,
    total_seconds,
)
from chronikwerk.operations.ticket_stores import (
    release_ticket,
    try_acquire_ticket,
    try_claim_delivery_id,
)
from chronikwerk.zammad.gateway import AsyncZammadClient

log = structlog.get_logger(__name__)


def build_ticket_processor(options: ArchiveRuntimeOptions) -> TicketProcessor:
    """Bind immutable runtime options once for process-local scheduled jobs."""

    async def process(delivery_id: str | None, payload: dict[str, Any]) -> ArchiveOutcome:
        return await process_ticket(delivery_id, payload, options)

    return process


TicketProcessor = Callable[[str | None, dict[str, Any]], Awaitable[ArchiveOutcome]]


async def process_ticket(
    delivery_id: str | None,
    payload: dict[str, Any],
    options: ArchiveRuntimeOptions,
) -> ArchiveOutcome:
    """Orchestrate the full ticket archival pipeline for a single ingest payload."""
    raw_request_id = payload.get(REQUEST_ID_KEY)
    ticket_id = extract_ticket_id(payload)
    if ticket_id is None:
        request_id = raw_request_id if isinstance(raw_request_id, str) else None
        log.info("process_ticket.skip_no_ticket_id", request_id=request_id)
        skipped_total.labels(reason="no_ticket_id").inc()
        attempt = ArchiveAttempt(
            runtime=options,
            ticket_id=0,
            delivery_id=delivery_id,
            request_id=request_id,
        )
        record_history(attempt, status="skipped_no_ticket_id")
        return ArchiveOutcome(status="skipped_no_ticket_id", ticket_id=None)

    request_id = (
        raw_request_id if isinstance(raw_request_id, str) and raw_request_id.strip() else None
    )
    attempt = ArchiveAttempt(
        runtime=options,
        ticket_id=ticket_id,
        delivery_id=delivery_id,
        request_id=request_id,
    )
    record_history(attempt, status="running")

    with structlog.contextvars.bound_contextvars(**_bound_context(attempt)):
        return await _process_with_ticket_lock(attempt, payload=payload)


def _bound_context(attempt: ArchiveAttempt) -> dict[str, object]:
    bound: dict[str, object] = {"ticket_id": attempt.ticket_id}
    if attempt.delivery_id:
        bound["delivery_id"] = attempt.delivery_id
    if attempt.request_id:
        bound["request_id"] = attempt.request_id
    return bound


def _force_reprocess_requested(payload: dict[str, Any]) -> bool:
    return payload.get(FORCE_REPROCESS_KEY) is True


async def _process_with_ticket_lock(
    attempt: ArchiveAttempt,
    *,
    payload: dict[str, Any],
) -> ArchiveOutcome:
    acquired = await try_acquire_ticket(attempt.ticket_id)
    if not acquired:
        return await _skip_in_flight(attempt)

    try:
        claimed = await _claim_delivery_or_skip(attempt)
        if claimed is not None:
            return claimed
        return await _process_ticket_with_client(attempt, payload=payload)
    finally:
        await _release_ticket_lock(attempt)


async def _skip_in_flight(attempt: ArchiveAttempt) -> ArchiveOutcome:
    """Return a skip result when another worker is already processing this ticket."""
    log.info(
        "process_ticket.skip_ticket_in_flight",
        ticket_id=attempt.ticket_id,
        delivery_id=attempt.delivery_id,
    )
    skipped_total.labels(reason="in_flight").inc()
    record_history(attempt, status="skipped_in_flight")
    return ArchiveOutcome(status="skipped_in_flight", ticket_id=attempt.ticket_id)


async def _claim_delivery_or_skip(attempt: ArchiveAttempt) -> ArchiveOutcome | None:
    """Enforce at-most-once delivery; return a skip result for a claimed delivery."""
    if not attempt.delivery_id:
        return None
    if await try_claim_delivery_id(
        attempt.runtime.workflow.delivery_id_ttl_seconds, attempt.delivery_id
    ):
        return None

    log.info(
        "process_ticket.skip_delivery_id_seen",
        ticket_id=attempt.ticket_id,
        delivery_id=attempt.delivery_id,
    )
    skipped_total.labels(reason="idempotency").inc()
    record_history(attempt, status="skipped_idempotency")
    return ArchiveOutcome(status="skipped_idempotency", ticket_id=attempt.ticket_id)


async def _process_ticket_with_client(
    attempt: ArchiveAttempt,
    *,
    payload: dict[str, Any],
) -> ArchiveOutcome:
    """Open a Zammad client session and preserve the job-level error boundary."""
    async with AsyncZammadClient(connection=attempt.runtime.connection) as client:
        request = ArchivePipelineRequest(
            client=client,
            attempt=attempt,
            payload=payload,
            force_reprocess=_force_reprocess_requested(payload),
        )
        total_start = perf_counter()
        observe_total = True
        try:
            result, observe_total = await _run_pipeline_with_error_boundary(request)
            return result
        finally:
            if observe_total:
                total_seconds.observe(perf_counter() - total_start)


async def _run_pipeline_with_error_boundary(
    request: ArchivePipelineRequest,
) -> tuple[ArchiveOutcome, bool]:
    try:
        return await run_ticket_pipeline(request)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return (
            await _handle_pipeline_failure(request, exc),
            True,
        )


async def _handle_pipeline_failure(
    request: ArchivePipelineRequest,
    exc: Exception,
) -> ArchiveOutcome:
    try:
        return await handle_ticket_pipeline_exception(
            client=request.client,
            attempt=request.attempt,
            trigger_tag=request.attempt.runtime.workflow.trigger_tag,
            exc=exc,
        )
    except asyncio.CancelledError:
        await cleanup_cancelled_pipeline(request)
        raise


async def _release_ticket_lock(attempt: ArchiveAttempt) -> None:
    try:
        await asyncio.shield(release_ticket(attempt.ticket_id))
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception(
            "process_ticket.release_ticket_failed",
            ticket_id=attempt.ticket_id,
            request_id=attempt.request_id,
            delivery_id=attempt.delivery_id,
        )
