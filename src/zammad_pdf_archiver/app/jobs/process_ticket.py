from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any

import structlog

from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient
from zammad_pdf_archiver.app.constants import REQUEST_ID_KEY
from zammad_pdf_archiver.app.jobs.history import record_history_event
from zammad_pdf_archiver.app.jobs.retry_policy import classify
from zammad_pdf_archiver.app.jobs.ticket_fetcher import fetch_ticket_data
from zammad_pdf_archiver.app.jobs.ticket_notes import (
    action_hint,
    concise_exc_message,
    error_code_and_hint,
    error_note_html,
    success_note_html,
)
from zammad_pdf_archiver.app.jobs.ticket_path import (
    determine_username,
    parse_archive_path_segments,
)
from zammad_pdf_archiver.app.jobs.ticket_renderer import build_and_render_pdf
from zammad_pdf_archiver.app.jobs.ticket_storage import (
    compute_storage_paths,
    store_ticket_files,
)
from zammad_pdf_archiver.app.jobs.ticket_stores import (
    aclose_stores,
    release_ticket,
    try_acquire_ticket,
    try_claim_delivery_id,
)
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError
from zammad_pdf_archiver.domain.state_machine import (
    PROCESSING_TAG,
    TRIGGER_TAG,
    apply_done,
    apply_error,
    apply_processing,
    should_process,
)
from zammad_pdf_archiver.domain.ticket_id import extract_ticket_id
from zammad_pdf_archiver.domain.ticket_utils import ticket_custom_fields
from zammad_pdf_archiver.domain.time_utils import format_timestamp_utc, now_utc
from zammad_pdf_archiver.observability.metrics import (
    failed_total,
    processed_total,
    skipped_total,
    total_seconds,
)

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ProcessTicketResult:
    status: str
    ticket_id: int | None
    classification: str | None = None
    message: str = ""


def _now_utc() -> datetime:
    return now_utc()


def _format_timestamp_utc(dt: datetime) -> str:
    return format_timestamp_utc(dt)


async def _record_history(
    settings: Settings,
    *,
    status: str,
    ticket_id: int | None,
    classification: str | None = None,
    message: str = "",
    delivery_id: str | None = None,
    request_id: str | None = None,
) -> None:
    try:
        await record_history_event(
            settings,
            status=status,
            ticket_id=ticket_id,
            classification=classification,
            message=message,
            delivery_id=delivery_id,
            request_id=request_id,
        )
    except Exception:
        log.debug("process_ticket.history_record_failed", status=status, ticket_id=ticket_id)


async def _apply_done_with_backoff(
    client: AsyncZammadClient,
    *,
    ticket_id: int,
    trigger_tag: str,
    max_retries: int = 3,
) -> None:
    for attempt in range(max_retries):
        try:
            await apply_done(client, ticket_id, trigger_tag=trigger_tag)
            return
        except Exception:
            if attempt == max_retries - 1:
                raise
            await asyncio.sleep(0.5 * (2**attempt))


async def _apply_error_with_retry(
    client: AsyncZammadClient,
    *,
    ticket_id: int,
    keep_trigger: bool,
    trigger_tag: str,
) -> None:
    try:
        await apply_error(
            client,
            ticket_id,
            keep_trigger=keep_trigger,
            trigger_tag=trigger_tag,
        )
    except Exception:
        await asyncio.sleep(0.3)
        await apply_error(
            client,
            ticket_id,
            keep_trigger=keep_trigger,
            trigger_tag=trigger_tag,
        )


async def process_ticket(
    delivery_id: str | None,
    payload: dict[str, Any],
    settings: Settings,
) -> ProcessTicketResult:
    request_id = payload.get(REQUEST_ID_KEY)
    ticket_id = extract_ticket_id(payload)
    if ticket_id is None:
        return await _skip_no_ticket_id(settings, request_id=request_id)

    if not isinstance(request_id, str) or not request_id.strip():
        request_id = None

    bound = _bound_context(ticket_id=ticket_id, delivery_id=delivery_id, request_id=request_id)
    with structlog.contextvars.bound_contextvars(**bound):
        return await _process_with_ticket_lock(
            settings=settings,
            payload=payload,
            ticket_id=ticket_id,
            delivery_id=delivery_id,
            request_id=request_id,
        )


async def _skip_no_ticket_id(settings: Settings, *, request_id: Any) -> ProcessTicketResult:
    log.info("process_ticket.skip_no_ticket_id", request_id=request_id)
    skipped_total.labels(reason="no_ticket_id").inc()
    await _record_history(
        settings,
        status="skipped_no_ticket_id",
        ticket_id=None,
        request_id=request_id if isinstance(request_id, str) else None,
    )
    return ProcessTicketResult(status="skipped_no_ticket_id", ticket_id=None)


def _bound_context(
    *,
    ticket_id: int,
    delivery_id: str | None,
    request_id: str | None,
) -> dict[str, object]:
    bound: dict[str, object] = {"ticket_id": ticket_id}
    if delivery_id:
        bound["delivery_id"] = delivery_id
    if request_id:
        bound["request_id"] = request_id
    return bound


async def _process_with_ticket_lock(
    *,
    settings: Settings,
    payload: dict[str, Any],
    ticket_id: int,
    delivery_id: str | None,
    request_id: str | None,
) -> ProcessTicketResult:
    acquired = await try_acquire_ticket(settings, ticket_id)
    if not acquired:
        return await _skip_in_flight(
            settings,
            ticket_id=ticket_id,
            delivery_id=delivery_id,
            request_id=request_id,
        )

    try:
        claimed = await _claim_delivery_or_skip(
            settings=settings,
            ticket_id=ticket_id,
            delivery_id=delivery_id,
            request_id=request_id,
        )
        if claimed is not None:
            return claimed
        return await _process_ticket_with_client(
            settings=settings,
            payload=payload,
            ticket_id=ticket_id,
            delivery_id=delivery_id,
            request_id=request_id,
        )
    finally:
        await _release_ticket_lock(
            settings,
            ticket_id=ticket_id,
            delivery_id=delivery_id,
            request_id=request_id,
        )


async def _skip_in_flight(
    settings: Settings,
    *,
    ticket_id: int,
    delivery_id: str | None,
    request_id: str | None,
) -> ProcessTicketResult:
    log.info(
        "process_ticket.skip_ticket_in_flight",
        ticket_id=ticket_id,
        delivery_id=delivery_id,
    )
    skipped_total.labels(reason="in_flight").inc()
    await _record_history(
        settings,
        status="skipped_in_flight",
        ticket_id=ticket_id,
        delivery_id=delivery_id,
        request_id=request_id,
    )
    return ProcessTicketResult(status="skipped_in_flight", ticket_id=ticket_id)


async def _claim_delivery_or_skip(
    *,
    settings: Settings,
    ticket_id: int,
    delivery_id: str | None,
    request_id: str | None,
) -> ProcessTicketResult | None:
    if not delivery_id:
        return None
    if await try_claim_delivery_id(settings, delivery_id):
        return None

    log.info(
        "process_ticket.skip_delivery_id_seen",
        ticket_id=ticket_id,
        delivery_id=delivery_id,
    )
    skipped_total.labels(reason="idempotency").inc()
    await _record_history(
        settings,
        status="skipped_idempotency",
        ticket_id=ticket_id,
        delivery_id=delivery_id,
        request_id=request_id,
    )
    return ProcessTicketResult(status="skipped_idempotency", ticket_id=ticket_id)


async def _process_ticket_with_client(
    *,
    settings: Settings,
    payload: dict[str, Any],
    ticket_id: int,
    delivery_id: str | None,
    request_id: str | None,
) -> ProcessTicketResult:
    trigger_tag = str(settings.workflow.trigger_tag).strip() or TRIGGER_TAG
    require_trigger_tag = bool(settings.workflow.require_tag)

    async with AsyncZammadClient(
        base_url=str(settings.zammad.base_url),
        api_token=settings.zammad.api_token.get_secret_value(),
        timeout_seconds=settings.zammad.timeout_seconds,
        verify_tls=settings.zammad.verify_tls,
        trust_env=settings.hardening.transport.trust_env,
    ) as client:
        observe_total = True
        total_start = perf_counter()
        try:
            result, observe_total = await _run_ticket_pipeline(
                client=client,
                settings=settings,
                payload=payload,
                ticket_id=ticket_id,
                delivery_id=delivery_id,
                request_id=request_id,
                trigger_tag=trigger_tag,
                require_trigger_tag=require_trigger_tag,
            )
            return result
        except asyncio.CancelledError:
            # Cancellation during shutdown should not mutate ticket state.
            raise
        except Exception as exc:
            return await _handle_ticket_pipeline_exception(
                client=client,
                settings=settings,
                ticket_id=ticket_id,
                delivery_id=delivery_id,
                request_id=request_id,
                trigger_tag=trigger_tag,
                exc=exc,
            )
        finally:
            if observe_total:
                total_seconds.observe(perf_counter() - total_start)


async def _run_ticket_pipeline(
    *,
    client: AsyncZammadClient,
    settings: Settings,
    payload: dict[str, Any],
    ticket_id: int,
    delivery_id: str | None,
    request_id: str | None,
    trigger_tag: str,
    require_trigger_tag: bool,
) -> tuple[ProcessTicketResult, bool]:
    ticket_data = await fetch_ticket_data(client, ticket_id)
    if not should_process(
        ticket_data.tags.root,
        trigger_tag=trigger_tag,
        require_trigger_tag=require_trigger_tag,
    ):
        return (
            await _skip_not_triggered(
                settings,
                ticket_id=ticket_id,
                delivery_id=delivery_id,
                request_id=request_id,
                tags=ticket_data.tags.root,
            ),
            False,
        )

    await apply_processing(client, ticket_id, trigger_tag=trigger_tag)

    custom_fields = ticket_custom_fields(ticket_data.ticket)
    username = determine_username(
        ticket=ticket_data.ticket,
        payload=payload,
        custom_fields=custom_fields,
        mode_field_name=settings.fields.archive_user_mode,
        archive_user_field_name=settings.fields.archive_user,
    )

    segments = parse_archive_path_segments(custom_fields.get(settings.fields.archive_path))
    now = _now_utc()
    storage_paths = compute_storage_paths(
        storage_root=settings.storage.root,
        username=username,
        archive_path_segments=segments,
        allow_prefixes=settings.storage.path_policy.allow_prefixes,
        filename_pattern=settings.storage.path_policy.filename_pattern,
        ticket_number=ticket_data.ticket.number,
        date_iso=now.date().isoformat(),
    )

    render_result = await build_and_render_pdf(
        client,
        ticket_data.ticket,
        ticket_data.tags,
        ticket_id,
        settings,
    )
    storage_result = store_ticket_files(
        pdf_bytes=render_result.pdf_bytes,
        snapshot=render_result.snapshot,
        paths=storage_paths,
        ticket_id=ticket_data.ticket.id,
        now=now,
        settings=settings,
    )
    await _acknowledge_success_if_enabled(
        client=client,
        settings=settings,
        ticket_id=ticket_id,
        request_id=request_id,
        delivery_id=delivery_id,
        now=now,
        storage_dir=str(storage_result.target_path.parent),
        filename=storage_result.target_path.name,
        sidecar_path=str(storage_result.sidecar_path),
        size_bytes=storage_result.size_bytes,
        sha256_hex=storage_result.sha256_hex,
    )
    await _apply_done_best_effort(client, ticket_id=ticket_id, trigger_tag=trigger_tag)

    processed_total.inc()
    await _record_history(
        settings,
        status="processed",
        ticket_id=ticket_id,
        delivery_id=delivery_id,
        request_id=request_id,
    )
    log.info(
        "process_ticket.done",
        ticket_id=ticket_id,
        storage_path=str(storage_result.target_path),
        request_id=request_id,
        delivery_id=delivery_id,
    )
    return ProcessTicketResult(status="processed", ticket_id=ticket_id), True


async def _skip_not_triggered(
    settings: Settings,
    *,
    ticket_id: int,
    delivery_id: str | None,
    request_id: str | None,
    tags: list[str],
) -> ProcessTicketResult:
    log.info(
        "process_ticket.skip_should_not_process",
        ticket_id=ticket_id,
        tags=tags,
    )
    skipped_total.labels(reason="not_triggered").inc()
    await _record_history(
        settings,
        status="skipped_not_triggered",
        ticket_id=ticket_id,
        delivery_id=delivery_id,
        request_id=request_id,
    )
    return ProcessTicketResult(
        status="skipped_not_triggered",
        ticket_id=ticket_id,
    )


async def _acknowledge_success_if_enabled(
    *,
    client: AsyncZammadClient,
    settings: Settings,
    ticket_id: int,
    request_id: str | None,
    delivery_id: str | None,
    now: datetime,
    storage_dir: str,
    filename: str,
    sidecar_path: str,
    size_bytes: int,
    sha256_hex: str,
) -> None:
    if not settings.workflow.acknowledge_on_success:
        return
    await client.create_internal_article(
        ticket_id,
        f"PDF archived ({VERSION})",
        success_note_html(
            storage_dir=storage_dir,
            filename=filename,
            sidecar_path=sidecar_path,
            size_bytes=size_bytes,
            sha256_hex=sha256_hex,
            request_id=request_id,
            delivery_id=delivery_id,
            timestamp_utc=_format_timestamp_utc(now),
        ),
    )


async def _apply_done_best_effort(
    client: AsyncZammadClient, *, ticket_id: int, trigger_tag: str
) -> None:
    try:
        await _apply_done_with_backoff(client, ticket_id=ticket_id, trigger_tag=trigger_tag)
    except Exception:
        log.exception("process_ticket.apply_done_failed", ticket_id=ticket_id)


async def _handle_ticket_pipeline_exception(
    *,
    client: AsyncZammadClient,
    settings: Settings,
    ticket_id: int,
    delivery_id: str | None,
    request_id: str | None,
    trigger_tag: str,
    exc: Exception,
) -> ProcessTicketResult:
    failed_total.inc()
    classified = classify(exc)
    classification_label = _classification_label(classified)
    msg = concise_exc_message(exc)
    action = action_hint(exc, classified=classified) if classified is not None else ""
    code, hint = _error_code_hint(exc, classified=classified)

    log.exception(
        "process_ticket.error",
        ticket_id=ticket_id,
        request_id=request_id,
        delivery_id=delivery_id,
        classification=classification_label,
        code=code or None,
        hint=hint or None,
    )

    await _post_error_note(
        client=client,
        ticket_id=ticket_id,
        request_id=request_id,
        delivery_id=delivery_id,
        classification_label=classification_label,
        msg=msg,
        action=action,
        code=code,
        hint=hint,
    )
    await _apply_error_and_cleanup_processing_tag(
        client=client,
        ticket_id=ticket_id,
        request_id=request_id,
        delivery_id=delivery_id,
        classification_label=classification_label,
        classified=classified,
        trigger_tag=trigger_tag,
    )

    status = (
        "failed_transient"
        if classified is not None and isinstance(classified, TransientError)
        else "failed_permanent"
    )
    await _record_history(
        settings,
        status=status,
        ticket_id=ticket_id,
        classification=classification_label,
        message=msg,
        delivery_id=delivery_id,
        request_id=request_id,
    )
    return ProcessTicketResult(
        status=status,
        ticket_id=ticket_id,
        classification=classification_label,
        message=msg,
    )


def _classification_label(classified: TransientError | PermanentError | None) -> str:
    is_transient = classified is not None and isinstance(classified, TransientError)
    return "Transient" if is_transient else "Permanent"


def _error_code_hint(
    exc: BaseException, *, classified: TransientError | PermanentError | None
) -> tuple[str, str]:
    if classified is not None and isinstance(classified, PermanentError):
        return error_code_and_hint(exc)
    return "", ""


async def _post_error_note(
    *,
    client: AsyncZammadClient,
    ticket_id: int,
    request_id: str | None,
    delivery_id: str | None,
    classification_label: str,
    msg: str,
    action: str,
    code: str,
    hint: str,
) -> None:
    now = _now_utc()
    try:
        await client.create_internal_article(
            ticket_id,
            f"PDF archiver error ({VERSION})",
            error_note_html(
                classification=classification_label,
                message=msg,
                action=action,
                request_id=request_id,
                delivery_id=delivery_id,
                timestamp_utc=_format_timestamp_utc(now),
                code=code,
                hint=hint,
            ),
        )
    except Exception:
        log.exception(
            "process_ticket.error_note_failed",
            ticket_id=ticket_id,
            request_id=request_id,
            delivery_id=delivery_id,
            classification=classification_label,
        )


async def _apply_error_and_cleanup_processing_tag(
    *,
    client: AsyncZammadClient,
    ticket_id: int,
    request_id: str | None,
    delivery_id: str | None,
    classification_label: str,
    classified: TransientError | PermanentError | None,
    trigger_tag: str,
) -> None:
    try:
        keep_trigger = classified is not None and isinstance(classified, TransientError)
        await _apply_error_with_retry(
            client,
            ticket_id=ticket_id,
            keep_trigger=keep_trigger,
            trigger_tag=trigger_tag,
        )
    except Exception:
        log.exception(
            "process_ticket.apply_error_failed",
            ticket_id=ticket_id,
            request_id=request_id,
            delivery_id=delivery_id,
            classification=classification_label,
        )

    try:
        await client.remove_tag(ticket_id, PROCESSING_TAG)
    except Exception:
        log.exception(
            "process_ticket.processing_tag_cleanup_failed",
            ticket_id=ticket_id,
            request_id=request_id,
            delivery_id=delivery_id,
            classification=classification_label,
        )


async def _release_ticket_lock(
    settings: Settings,
    *,
    ticket_id: int,
    delivery_id: str | None,
    request_id: str | None,
) -> None:
    try:
        await asyncio.shield(release_ticket(settings, ticket_id))
    except Exception:
        log.exception(
            "process_ticket.release_ticket_failed",
            ticket_id=ticket_id,
            request_id=request_id,
            delivery_id=delivery_id,
        )


async def aclose_redis_stores() -> None:
    """Backwards-compatible alias for legacy imports."""
    await aclose_stores()
