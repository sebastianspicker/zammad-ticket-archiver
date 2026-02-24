from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from time import perf_counter
from typing import Any

import structlog

from zammad_pdf_archiver._version import VERSION
from zammad_pdf_archiver.adapters.pdf.render_pdf import render_pdf
from zammad_pdf_archiver.adapters.signing.sign_pdf import sign_pdf
from zammad_pdf_archiver.adapters.snapshot.build_snapshot import (
    build_snapshot,
    enrich_attachment_content,
)
from zammad_pdf_archiver.adapters.storage.fs_storage import (
    ensure_dir,
    move_file_within_root,
    write_atomic_bytes,
    write_bytes,
)
from zammad_pdf_archiver.adapters.storage.layout import (
    build_filename_from_pattern,
    build_target_dir,
)
from zammad_pdf_archiver.adapters.zammad.client import AsyncZammadClient
from zammad_pdf_archiver.adapters.zammad.errors import (
    AuthError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from zammad_pdf_archiver.app.jobs.retry_policy import classify
from zammad_pdf_archiver.config.redact import scrub_secrets_in_text
from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError
from zammad_pdf_archiver.domain.path_policy import sanitize_segment
from zammad_pdf_archiver.app.constants import REQUEST_ID_KEY
from zammad_pdf_archiver.app.jobs.ticket_notes import (
    success_note_html,
    error_note_html,
    error_code_and_hint,
    concise_exc_message,
    action_hint,
)
from zammad_pdf_archiver.app.jobs.ticket_path import (
    determine_username,
    parse_archive_path_segments,
)
from zammad_pdf_archiver.app.jobs.ticket_stores import (
    try_claim_delivery_id,
    try_acquire_ticket,
    release_ticket,
)
from zammad_pdf_archiver.domain.audit import build_audit_record, compute_sha256
from zammad_pdf_archiver.domain.redis_delivery_id import RedisDeliveryIdStore

from zammad_pdf_archiver.domain.snapshot_models import Snapshot
from zammad_pdf_archiver.domain.state_machine import (
    PROCESSING_TAG,
    TRIGGER_TAG,
    apply_done,
    apply_error,
    apply_processing,
    should_process,
)
from zammad_pdf_archiver.domain.ticket_id import coerce_ticket_id, extract_ticket_id
from zammad_pdf_archiver.domain.ticket_utils import ticket_custom_fields
from zammad_pdf_archiver.adapters.zammad.models import Ticket
from zammad_pdf_archiver.observability.metrics import (
    failed_total,
    processed_total,
    render_seconds,
    sign_seconds,
    total_seconds,
    skipped_total,
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
            if delivery_id:
                if not await try_claim_delivery_id(settings, delivery_id):
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
                    ticket = await client.get_ticket(ticket_id)
                    tags = await client.list_tags(ticket_id)

                    if not should_process(
                        tags.root,
                        trigger_tag=trigger_tag,
                        require_trigger_tag=require_trigger_tag,
                    ):
                        observe_total = False
                        log.info(
                            "process_ticket.skip_should_not_process",
                            ticket_id=ticket_id,
                            tags=tags.root,
                        )
                        skipped_total.labels(reason="not_triggered").inc()
                        return

                    await apply_processing(client, ticket_id, trigger_tag=trigger_tag)

                    custom_fields = ticket_custom_fields(ticket)
                    username = determine_username(
                        ticket=ticket,
                        payload=payload,
                        custom_fields=custom_fields,
                        mode_field_name=settings.fields.archive_user_mode,
                        archive_user_field_name=settings.fields.archive_user,
                    )

                    segments = parse_archive_path_segments(
                        custom_fields.get(settings.fields.archive_path)
                    )
                    target_dir = build_target_dir(
                        settings.storage.root,
                        username,
                        segments,
                        allow_prefixes=settings.storage.path_policy.allow_prefixes,
                    )

                    now = datetime.now(UTC)
                    date_iso = now.date().isoformat()
                    filename = build_filename_from_pattern(
                        settings.storage.path_policy.filename_pattern,
                        ticket_number=ticket.number,
                        timestamp_utc=date_iso,
                    )
                    target_path = target_dir / filename

                    snapshot = await build_snapshot(
                        client,
                        ticket_id,
                        ticket=ticket,
                        tags=tags,
                    )
                    # Bug #4/#10: cap_and_continue truncates articles and logs instead of failing.
                    max_articles = settings.pdf.max_articles
                    if (
                        getattr(settings.pdf, "article_limit_mode", "fail") == "cap_and_continue"
                        and max_articles > 0
                        and len(snapshot.articles) > max_articles
                    ):
                        log.warning(
                            "process_ticket.article_limit_capped",
                            ticket_id=ticket_id,
                            total=len(snapshot.articles),
                            cap=max_articles,
                        )
                        snapshot = Snapshot(
                            ticket=snapshot.ticket,
                            articles=snapshot.articles[:max_articles],
                        )
                    snapshot = await enrich_attachment_content(
                        snapshot,
                        client,
                        include_attachment_binary=settings.pdf.include_attachment_binary,
                        max_attachment_bytes_per_file=settings.pdf.max_attachment_bytes_per_file,
                        max_total_attachment_bytes=settings.pdf.max_total_attachment_bytes,
                    )
                    render_start = perf_counter()
                    pdf_bytes = render_pdf(
                        snapshot,
                        settings.pdf.template,
                        max_articles=settings.pdf.max_articles,
                        locale=settings.pdf.locale,
                        timezone=settings.pdf.timezone,
                        templates_root=settings.pdf.templates_root,
                    )
                    render_seconds.observe(perf_counter() - render_start)

                    if settings.signing.enabled:
                        sign_start = perf_counter()
                        # pyHanko's synchronous signing helper uses asyncio.run() internally.
                        # Offload to a worker thread to avoid:
                        # "asyncio.run() cannot be called from a running event loop".
                        pdf_bytes = await asyncio.to_thread(sign_pdf, pdf_bytes, settings)
                        sign_seconds.observe(perf_counter() - sign_start)

                    sha256_hex = compute_sha256(pdf_bytes)
                    size_bytes = len(pdf_bytes)

                    # P0: Atomic Archival grouping using a temporary directory.
                    # We write all files to a transient adjacent folder, then move them to
                    # their final locations. This ensures a crash mid-job doesn't leave
                    # a half-archived ticket at the final destination.
                    temp_archive_root = target_path.parent / f".tmp-archiving-{ticket_id}-{uuid.uuid4().hex[:8]}"
                    attachment_entries: list[dict[str, Any]] = []
                    sidecar_path = target_path.with_name(target_path.name + ".json")

                    try:
                        ensure_dir(temp_archive_root)
                        temp_pdf_path = temp_archive_root / target_path.name
                        temp_sidecar_path = temp_archive_root / sidecar_path.name
                        temp_attachments_dir = temp_archive_root / "attachments"

                        attachments_dir = target_path.parent / "attachments"
                        snapshot_articles = getattr(snapshot, "articles", None)

                        if isinstance(snapshot_articles, list) and snapshot_articles:
                            has_attachments = any(
                                att.content is not None
                                for article in snapshot_articles
                                for att in article.attachments
                            )
                            if has_attachments:
                                ensure_dir(temp_attachments_dir)
                                for article in snapshot_articles:
                                    for att in article.attachments:
                                        if att.content is None:
                                            continue
                                        safe_name = sanitize_segment(
                                            f"{article.id}_{att.attachment_id or 0}_{att.filename or 'bin'}"
                                        ) or f"article_{article.id}_{att.attachment_id or 0}"
                                        attach_temp_path = temp_attachments_dir / safe_name
                                        write_bytes(
                                            attach_temp_path,
                                            att.content,
                                            fsync=settings.storage.fsync,
                                            storage_root=settings.storage.root,
                                        )
                                        attachment_entries.append(
                                            {
                                                "storage_path": str(attachments_dir / safe_name),
                                                "article_id": article.id,
                                                "attachment_id": att.attachment_id,
                                                "filename": att.filename,
                                                "sha256": compute_sha256(att.content),
                                            }
                                        )

                        audit_record = build_audit_record(
                            ticket_id=ticket.id,
                            ticket_number=ticket.number,
                            title=ticket.title,
                            created_at=now,
                            storage_path=str(target_path),
                            sha256=sha256_hex,
                            signing_settings=settings.signing,
                            attachments=attachment_entries if attachment_entries else None,
                        )
                        audit_bytes = (
                            json.dumps(audit_record, ensure_ascii=False, sort_keys=True, indent=2)
                            + "\n"
                        ).encode("utf-8")

                        # Write PDF and sidecar into temp dir
                        write_bytes(
                            temp_pdf_path,
                            pdf_bytes,
                            fsync=settings.storage.fsync,
                            storage_root=settings.storage.root,
                        )
                        write_bytes(
                            temp_sidecar_path,
                            audit_bytes,
                            fsync=settings.storage.fsync,
                            storage_root=settings.storage.root,
                        )

                        # PHASE 2: ATOMIC "COMMIT" (Moves)
                        # We use move_file_within_root which performs rename (atomic on same FS).
                        if attachment_entries:
                            ensure_dir(attachments_dir)
                            for entry in attachment_entries:
                                # We need to recalculate the temp path or store it.
                                # Let's just use the filename from the storage_path.
                                fname = Path(entry["storage_path"]).name
                                move_file_within_root(
                                    temp_attachments_dir / fname,
                                    attachments_dir / fname,
                                    storage_root=settings.storage.root,
                                    fsync=settings.storage.fsync,
                                )

                        # Move PDF
                        move_file_within_root(
                            temp_pdf_path,
                            target_path,
                            storage_root=settings.storage.root,
                            fsync=settings.storage.fsync,
                        )

                        # Move Sidecar (Last: signals successful archival)
                        move_file_within_root(
                            temp_sidecar_path,
                            sidecar_path,
                            storage_root=settings.storage.root,
                            fsync=settings.storage.fsync,
                        )
                    finally:
                        if temp_archive_root.exists():
                            shutil.rmtree(temp_archive_root, ignore_errors=True)

                    if settings.workflow.acknowledge_on_success:
                        await client.create_internal_article(
                            ticket_id,
                            f"PDF archived ({VERSION})",
                            success_note_html(
                                storage_dir=str(target_path.parent),
                                filename=target_path.name,
                                sidecar_path=str(sidecar_path),
                                size_bytes=size_bytes,
                                sha256_hex=sha256_hex,
                                request_id=request_id,
                                delivery_id=delivery_id,
                                timestamp_utc=_format_timestamp_utc(now),
                            ),
                        )

                    try:
                        # Bug #P1-2: State desync. We use a slightly more aggressive retry
                        # for the final state change to decrease chance of "stuck" tickets.
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
                        storage_path=str(target_path),
                        request_id=request_id,
                        delivery_id=delivery_id,
                    )
                except BaseException as exc:
                    # Bug #15: catch BaseException so CancelledError runs cleanup too.
                    failed_total.inc()
                    classified = (
                        None if isinstance(exc, asyncio.CancelledError) else classify(exc)
                    )
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
                        keep_trigger = (
                            classified is not None and isinstance(classified, TransientError)
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

                    # Best-effort: ensure processing tag is removed.
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
                    # Bug #15: re-raise CancelledError after cleanup so task is properly cancelled.
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                finally:
                    if observe_total:
                        total_seconds.observe(perf_counter() - total_start)
        finally:
            # Bug #16: shield so cancellation during release doesn't leave ticket in in-flight set.
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
    """Close all persistent Redis connections. Called on application shutdown."""
    global _SHUTTING_DOWN
    async with _STORE_GUARD:
        _SHUTTING_DOWN = True
        for store in _REDIS_STORES.values():
            try:
                await store.aclose()
            except Exception:
                log.warning("process_ticket.redis_idempotency_aclose_failed")
        _REDIS_STORES.clear()

        for store in _REDIS_TICKET_LOCK_STORES.values():
            try:
                await store.aclose()
            except Exception:
                log.warning("process_ticket.redis_lock_aclose_failed")
        _REDIS_TICKET_LOCK_STORES.clear()
