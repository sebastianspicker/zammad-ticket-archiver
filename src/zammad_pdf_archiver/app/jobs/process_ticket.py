from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

import structlog

from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient
from zammad_pdf_archiver.app.constants import REQUEST_ID_KEY
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
from zammad_pdf_archiver.observability.metrics import (
    failed_total,
    processed_total,
    skipped_total,
    total_seconds,
)

log = structlog.get_logger(__name__)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _format_timestamp_utc(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


async def process_ticket(
    delivery_id: str | None,
    payload: dict[str, Any],
    settings: Settings,
) -> None:
    request_id = payload.get(REQUEST_ID_KEY)
    ticket_id = extract_ticket_id(payload)
    if ticket_id is None:
        log.info("process_ticket.skip_no_ticket_id", request_id=request_id)
        skipped_total.labels(reason="no_ticket_id").inc()
        return

    if not isinstance(request_id, str) or not request_id.strip():
        request_id = None

    bound: dict[str, object] = {"ticket_id": ticket_id}
    if delivery_id:
        bound["delivery_id"] = delivery_id
    if request_id:
        bound["request_id"] = request_id

    with structlog.contextvars.bound_contextvars(**bound):
        trigger_tag = str(settings.workflow.trigger_tag).strip() or TRIGGER_TAG
        require_trigger_tag = bool(settings.workflow.require_tag)

        acquired = await try_acquire_ticket(settings, ticket_id)
        if not acquired:
            log.info(
                "process_ticket.skip_ticket_in_flight",
                ticket_id=ticket_id,
                delivery_id=delivery_id,
            )
            skipped_total.labels(reason="in_flight").inc()
            return

        try:
            if delivery_id and not await try_claim_delivery_id(settings, delivery_id):
                log.info(
                    "process_ticket.skip_delivery_id_seen",
                    ticket_id=ticket_id,
                    delivery_id=delivery_id,
                )
                skipped_total.labels(reason="idempotency").inc()
                return

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
                    ticket_data = await fetch_ticket_data(client, ticket_id)

                    if not should_process(
                        ticket_data.tags.root,
                        trigger_tag=trigger_tag,
                        require_trigger_tag=require_trigger_tag,
                    ):
                        observe_total = False
                        log.info(
                            "process_ticket.skip_should_not_process",
                            ticket_id=ticket_id,
                            tags=ticket_data.tags.root,
                        )
                        skipped_total.labels(reason="not_triggered").inc()
                        return

                    await apply_processing(client, ticket_id, trigger_tag=trigger_tag)

                    custom_fields = ticket_custom_fields(ticket_data.ticket)
                    username = determine_username(
                        ticket=ticket_data.ticket,
                        payload=payload,
                        custom_fields=custom_fields,
                        mode_field_name=settings.fields.archive_user_mode,
                        archive_user_field_name=settings.fields.archive_user,
                    )

                    segments = parse_archive_path_segments(
                        custom_fields.get(settings.fields.archive_path)
                    )
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

                    if settings.workflow.acknowledge_on_success:
                        await client.create_internal_article(
                            ticket_id,
                            f"PDF archived ({VERSION})",
                            success_note_html(
                                storage_dir=str(storage_result.target_path.parent),
                                filename=storage_result.target_path.name,
                                sidecar_path=str(storage_result.sidecar_path),
                                size_bytes=storage_result.size_bytes,
                                sha256_hex=storage_result.sha256_hex,
                                request_id=request_id,
                                delivery_id=delivery_id,
                                timestamp_utc=_format_timestamp_utc(now),
                            ),
                        )

                    try:
                        max_zammad_retries = 3
                        for attempt in range(max_zammad_retries):
                            try:
                                await apply_done(client, ticket_id, trigger_tag=trigger_tag)
                                break
                            except Exception:
                                if attempt == max_zammad_retries - 1:
                                    raise
                                await asyncio.sleep(0.5 * (2**attempt))
                    except Exception:
                        log.exception("process_ticket.apply_done_failed", ticket_id=ticket_id)

                    processed_total.inc()
                    log.info(
                        "process_ticket.done",
                        ticket_id=ticket_id,
                        storage_path=str(storage_result.target_path),
                        request_id=request_id,
                        delivery_id=delivery_id,
                    )
                except BaseException as exc:
                    failed_total.inc()
                    classified = None if isinstance(exc, asyncio.CancelledError) else classify(exc)
                    classification_label = (
                        "Transient"
                        if classified is not None and isinstance(classified, TransientError)
                        else "Permanent"
                    )
                    msg = concise_exc_message(exc)
                    action = (
                        action_hint(exc, classified=classified) if classified is not None else ""
                    )
                    code, hint = "", ""
                    if classified is not None and isinstance(classified, PermanentError):
                        code, hint = error_code_and_hint(exc)
                    log.exception(
                        "process_ticket.error",
                        ticket_id=ticket_id,
                        request_id=request_id,
                        delivery_id=delivery_id,
                        classification=classification_label,
                        code=code or None,
                        hint=hint or None,
                    )

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

                    try:
                        keep_trigger = classified is not None and isinstance(
                            classified, TransientError
                        )
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

                    if isinstance(exc, asyncio.CancelledError):
                        raise
                finally:
                    if observe_total:
                        total_seconds.observe(perf_counter() - total_start)
        finally:
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
