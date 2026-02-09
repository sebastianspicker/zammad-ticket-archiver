from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from html import escape
from time import perf_counter
from typing import Any

import structlog

from zammad_pdf_archiver.adapters.pdf.render_pdf import render_pdf
from zammad_pdf_archiver.adapters.signing.sign_pdf import sign_pdf
from zammad_pdf_archiver.adapters.snapshot.build_snapshot import build_snapshot
from zammad_pdf_archiver.adapters.storage.fs_storage import write_atomic_bytes, write_bytes
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
from zammad_pdf_archiver.domain.audit import build_audit_record, compute_sha256
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError
from zammad_pdf_archiver.domain.idempotency import InMemoryTTLSet
from zammad_pdf_archiver.domain.state_machine import (
    PROCESSING_TAG,
    TRIGGER_TAG,
    apply_done,
    apply_error,
    apply_processing,
    should_process,
)
from zammad_pdf_archiver.domain.ticket_id import coerce_ticket_id
from zammad_pdf_archiver.observability.metrics import (
    failed_total,
    processed_total,
    render_seconds,
    sign_seconds,
    total_seconds,
)

_DELIVERY_ID_SETS: dict[int, InMemoryTTLSet] = {}
_DELIVERY_ID_SETS_GUARD = asyncio.Lock()
_IN_FLIGHT_TICKETS: set[int] = set()
_IN_FLIGHT_TICKETS_GUARD = asyncio.Lock()
_REQUEST_ID_KEY = "_request_id"

log = structlog.get_logger(__name__)


async def _claim_delivery_id(settings: Settings, delivery_id: str) -> bool:
    """
    Atomically check and register delivery_id for idempotency.
    Returns True if this delivery was newly claimed (caller should proceed),
    False if already seen (caller should skip).
    """
    ttl = int(settings.workflow.delivery_id_ttl_seconds)
    if ttl <= 0:
        return True
    async with _DELIVERY_ID_SETS_GUARD:
        store = _DELIVERY_ID_SETS.get(ttl)
        if store is None:
            store = InMemoryTTLSet(ttl_seconds=float(ttl))
            _DELIVERY_ID_SETS[ttl] = store
        if store.seen(delivery_id):
            return False
        store.add(delivery_id)
        return True


async def _try_acquire_ticket(ticket_id: int) -> bool:
    async with _IN_FLIGHT_TICKETS_GUARD:
        if ticket_id in _IN_FLIGHT_TICKETS:
            return False
        _IN_FLIGHT_TICKETS.add(ticket_id)
        return True


async def _release_ticket(ticket_id: int) -> None:
    async with _IN_FLIGHT_TICKETS_GUARD:
        _IN_FLIGHT_TICKETS.discard(ticket_id)


def _extract_ticket_id(payload: dict[str, Any]) -> int | None:
    ticket = payload.get("ticket")
    if isinstance(ticket, dict):
        value = ticket.get("id")
    else:
        value = payload.get("ticket_id")
    return coerce_ticket_id(value)


def _ticket_custom_fields(ticket: Any) -> dict[str, Any]:
    prefs = getattr(ticket, "preferences", None)
    if prefs is None:
        return {}
    fields = getattr(prefs, "custom_fields", None)
    if isinstance(fields, dict):
        return fields
    return {}


def _require_nonempty(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    out = value.strip()
    if not out:
        raise ValueError(f"{field} must be non-empty")
    return out


def _determine_username(
    *,
    ticket: Any,
    payload: dict[str, Any],
    custom_fields: dict[str, Any],
    mode_field_name: str,
) -> str:
    raw_mode = custom_fields.get(mode_field_name)
    mode = str(raw_mode).strip() if raw_mode is not None else "owner"

    if mode == "owner":
        owner = getattr(ticket, "owner", None)
        return _require_nonempty(getattr(owner, "login", None), field="ticket.owner.login")

    if mode == "current_agent":
        user = payload.get("user")
        if isinstance(user, dict):
            login = user.get("login")
            if isinstance(login, str) and login.strip():
                return login.strip()

        updated_by = getattr(ticket, "updated_by", None)
        return _require_nonempty(
            getattr(updated_by, "login", None),
            field="ticket.updated_by.login",
        )

    if mode == "fixed":
        return _require_nonempty(
            custom_fields.get("archive_user"),
            field="custom_fields.archive_user",
        )

    raise ValueError(f"unsupported archive_user_mode: {mode!r}")


def _parse_archive_path_segments(value: Any) -> list[str]:
    if value is None:
        raise ValueError("custom_fields.archive_path is missing")

    if isinstance(value, str):
        raw_parts = [p.strip() for p in value.split(">")]
        parts = [p for p in raw_parts if p]
    elif isinstance(value, list):
        parts = []
        for idx, item in enumerate(value):
            if not isinstance(item, str):
                raise ValueError(f"custom_fields.archive_path[{idx}] must be a string")
            item = item.strip()
            if item:
                parts.append(item)
    else:
        raise ValueError("custom_fields.archive_path must be a string or list of strings")

    if not parts:
        raise ValueError(
            "custom_fields.archive_path must not be empty after sanitization "
            "(all segments were empty or whitespace-only)"
        )

    return parts


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _format_timestamp_utc(dt: datetime) -> str:
    # ISO 8601 with "Z" suffix.
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _success_note_html(
    *,
    storage_dir: str,
    filename: str,
    sidecar_path: str,
    size_bytes: int,
    sha256_hex: str,
    request_id: str | None,
    delivery_id: str | None,
    timestamp_utc: str,
) -> str:
    storage = escape(storage_dir)
    fname = escape(filename)
    sidecar = escape(sidecar_path)
    sha256 = escape(sha256_hex)
    rid = escape(request_id or "unknown")
    did = escape(delivery_id or "none")
    time_utc = escape(timestamp_utc)
    return (
        "<p><strong>PDF archived (v0.1)</strong></p>"
        "<ul>"
        f"<li>path: <code>{storage}</code></li>"
        f"<li>filename: <code>{fname}</code></li>"
        f"<li>audit_sidecar: <code>{sidecar}</code></li>"
        f"<li>size_bytes: <code>{size_bytes}</code></li>"
        f"<li>sha256: <code>{sha256}</code></li>"
        f"<li>request_id: <code>{rid}</code></li>"
        f"<li>delivery_id: <code>{did}</code></li>"
        f"<li>time_utc: <code>{time_utc}</code></li>"
        "</ul>"
    )


def _error_note_html(
    *,
    classification: str,
    message: str,
    action: str,
    request_id: str | None,
    delivery_id: str | None,
    timestamp_utc: str,
) -> str:
    rid = escape(request_id or "unknown")
    did = escape(delivery_id or "none")
    cls = escape(classification)
    msg = escape(message)
    act = escape(action)
    return (
        "<p><strong>PDF archiver error (v0.1)</strong></p>"
        "<ul>"
        f"<li>classification: <code>{cls}</code></li>"
        f"<li>error: <code>{msg}</code></li>"
        f"<li>action: <code>{act}</code></li>"
        f"<li>request_id: <code>{rid}</code></li>"
        f"<li>delivery_id: <code>{did}</code></li>"
        f"<li>time_utc: <code>{timestamp_utc}</code></li>"
        "</ul>"
    )


def _concise_exc_message(exc: BaseException) -> str:
    text = f"{exc.__class__.__name__}: {exc}"
    text = text.strip()
    text = scrub_secrets_in_text(text)
    return text[:500] if len(text) > 500 else text


def _action_hint(exc: BaseException, *, classified: TransientError | PermanentError) -> str:
    if isinstance(classified, TransientError):
        return (
            "Transient failure. Verify Zammad/TSA reachability and storage availability; "
            "the ticket keeps pdf:sign so a retry can be triggered by saving the ticket "
            "or reapplying the macro."
        )

    # PermanentError: aim for a concrete operator action.
    if isinstance(exc, AuthError):
        return "Fix Zammad API token/permissions (HTTP 401/403), then reapply the pdf:sign macro."
    if isinstance(exc, NotFoundError):
        return (
            "Ticket/resource not found in Zammad. Verify the ticket still exists, then reapply "
            "pdf:sign."
        )
    if isinstance(exc, (ServerError, RateLimitError)):
        return (
            "Upstream Zammad error was treated as permanent by policy. "
            "If the issue is resolved, reapply the pdf:sign macro to reprocess."
        )
    if isinstance(exc, PermissionError):
        return (
            "Storage permission denied. Check network share mount options, ownership, and ACLs, "
            "then reapply the pdf:sign macro."
        )
    if isinstance(exc, (ValueError, TypeError)):
        return (
            "Fix ticket fields / path policy validation, then reapply the pdf:sign macro "
            "(and optionally remove pdf:error for clarity)."
        )
    return (
        "Non-retryable failure by policy. Fix the underlying issue and reapply the pdf:sign macro "
        "(and optionally remove pdf:error)."
    )


async def process_ticket(
    delivery_id: str | None,
    payload: dict[str, Any],
    settings: Settings,
) -> None:
    ticket_id = _extract_ticket_id(payload)
    if ticket_id is None:
        log.info("process_ticket.skip_no_ticket_id")
        return

    request_id = payload.get(_REQUEST_ID_KEY)
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

        acquired = await _try_acquire_ticket(ticket_id)
        if not acquired:
            log.info(
                "process_ticket.skip_ticket_in_flight",
                ticket_id=ticket_id,
                delivery_id=delivery_id,
            )
            return

        try:
            if delivery_id:
                if not await _claim_delivery_id(settings, delivery_id):
                    log.info(
                        "process_ticket.skip_delivery_id_seen",
                        ticket_id=ticket_id,
                        delivery_id=delivery_id,
                    )
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
                        return

                    await apply_processing(client, ticket_id, trigger_tag=trigger_tag)

                    custom_fields = _ticket_custom_fields(ticket)
                    username = _determine_username(
                        ticket=ticket,
                        payload=payload,
                        custom_fields=custom_fields,
                        mode_field_name=settings.fields.archive_user_mode,
                    )

                    segments = _parse_archive_path_segments(
                        custom_fields.get(settings.fields.archive_path)
                    )
                    target_dir = build_target_dir(
                        settings.storage.root,
                        username,
                        segments,
                        allow_prefixes=settings.storage.path_policy.allow_prefixes,
                    )

                    now = _now_utc()
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
                    render_start = perf_counter()
                    pdf_bytes = render_pdf(
                        snapshot,
                        settings.pdf.template,
                        max_articles=settings.pdf.max_articles,
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

                    sidecar_path = target_path.with_name(target_path.name + ".json")
                    audit_record = build_audit_record(
                        ticket_id=ticket.id,
                        ticket_number=ticket.number,
                        title=ticket.title,
                        created_at=now,
                        storage_path=str(target_path),
                        sha256=sha256_hex,
                        signing_settings=settings.signing,
                    )
                    audit_bytes = (
                        json.dumps(audit_record, ensure_ascii=False, sort_keys=True, indent=2)
                        + "\n"
                    ).encode("utf-8")

                    writer = (
                        write_atomic_bytes if settings.storage.atomic_write else write_bytes
                    )
                    writer(
                        target_path,
                        pdf_bytes,
                        fsync=settings.storage.fsync,
                        storage_root=settings.storage.root,
                    )
                    writer(
                        sidecar_path,
                        audit_bytes,
                        fsync=settings.storage.fsync,
                        storage_root=settings.storage.root,
                    )

                    if settings.workflow.acknowledge_on_success:
                        await client.create_internal_article(
                            ticket_id,
                            "PDF archived (v0.1)",
                            _success_note_html(
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
                        await apply_done(client, ticket_id, trigger_tag=trigger_tag)
                    except Exception:
                        await asyncio.sleep(0.3)
                        await apply_done(client, ticket_id, trigger_tag=trigger_tag)
                    processed_total.inc()
                    log.info(
                        "process_ticket.done",
                        ticket_id=ticket_id,
                        storage_path=str(target_path),
                        request_id=request_id,
                        delivery_id=delivery_id,
                    )
                except Exception as exc:
                    failed_total.inc()
                    classified = classify(exc)
                    classification_label = (
                        "Transient" if isinstance(classified, TransientError) else "Permanent"
                    )
                    msg = _concise_exc_message(exc)
                    action = _action_hint(exc, classified=classified)
                    log.exception(
                        "process_ticket.error",
                        ticket_id=ticket_id,
                        request_id=request_id,
                        delivery_id=delivery_id,
                        classification=classification_label,
                    )

                    now = _now_utc()
                    try:
                        await client.create_internal_article(
                            ticket_id,
                            "PDF archiver error (v0.1)",
                            _error_note_html(
                                classification=classification_label,
                                message=msg,
                                action=action,
                                request_id=request_id,
                                delivery_id=delivery_id,
                                timestamp_utc=_format_timestamp_utc(now),
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
                        keep_trigger = isinstance(classified, TransientError)
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
                finally:
                    if observe_total:
                        total_seconds.observe(perf_counter() - total_start)
        finally:
            try:
                await _release_ticket(ticket_id)
            except Exception:
                log.exception(
                    "process_ticket.release_ticket_failed",
                    ticket_id=ticket_id,
                    request_id=request_id,
                    delivery_id=delivery_id,
                )
