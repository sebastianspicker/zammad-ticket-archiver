"""Failure handling for the archive application workflow."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from chronikwerk._version import VERSION
from chronikwerk.archiving import workflow
from chronikwerk.archiving.error_policy import classify
from chronikwerk.archiving.notes import (
    ErrorNotePayload,
    action_hint,
    concise_exc_message,
    error_code_and_hint,
    error_note_html,
)
from chronikwerk.archiving.retry import async_retry
from chronikwerk.failures import PermanentError, TransientError
from chronikwerk.operations.metrics import failed_total
from chronikwerk.timestamps import format_timestamp_utc, now_utc
from chronikwerk.zammad.gateway import AsyncZammadClient
from chronikwerk.zammad.workflow import apply_error

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class _ErrorNote:
    """Values rendered into one operator-visible archival failure note."""

    classification: str
    message: str
    action: str
    code: str
    hint: str


async def handle_ticket_pipeline_exception(
    *,
    client: AsyncZammadClient,
    attempt: workflow.ArchiveAttempt,
    trigger_tag: str,
    exc: Exception,
) -> workflow.ArchiveOutcome:
    """Classify an exception, post an error note, and update terminal tags."""
    failed_total.inc()
    classified = classify(exc)
    classification_label = _classification_label(classified)
    msg = concise_exc_message(exc)
    action = action_hint(exc, classified=classified) if classified is not None else ""
    code, hint = _error_code_hint(exc, classified=classified)

    note = _ErrorNote(
        classification=classification_label,
        message=msg,
        action=action,
        code=code,
        hint=hint,
    )
    _log_pipeline_error(attempt, classification_label=classification_label, code=code, hint=hint)

    await _post_error_note(
        client=client,
        attempt=attempt,
        note=note,
    )
    await _apply_error_and_cleanup_processing_tag(
        client=client,
        attempt=attempt,
        classification_label=classification_label,
        classified=classified,
        trigger_tag=trigger_tag,
    )

    status = _failure_status(classified)
    workflow.record_history(
        attempt,
        status=status,
        classification=classification_label,
        message=msg,
    )
    return workflow.ArchiveOutcome(
        status=status,
        ticket_id=attempt.ticket_id,
        classification=classification_label,
        message=msg,
    )


def _log_pipeline_error(
    attempt: workflow.ArchiveAttempt,
    *,
    classification_label: str,
    code: str,
    hint: str,
) -> None:
    log.exception(
        "process_ticket.error",
        ticket_id=attempt.ticket_id,
        request_id=attempt.request_id,
        delivery_id=attempt.delivery_id,
        classification=classification_label,
        code=code or None,
        hint=hint or None,
    )


def _failure_status(classified: TransientError | PermanentError | None) -> str:
    if classified is not None and isinstance(classified, TransientError):
        return "failed_transient"
    return "failed_permanent"


def _classification_label(classified: TransientError | PermanentError | None) -> str:
    """Map a classified error to its human-readable label for notes and metrics."""
    is_transient = classified is not None and isinstance(classified, TransientError)
    return "Transient" if is_transient else "Permanent"


def _error_code_hint(
    exc: BaseException, *, classified: TransientError | PermanentError | None
) -> tuple[str, str]:
    """Extract a structured error code and hint, but only for permanent errors."""
    if classified is not None and isinstance(classified, PermanentError):
        return error_code_and_hint(exc)
    return "", ""


async def _post_error_note(
    *,
    client: AsyncZammadClient,
    attempt: workflow.ArchiveAttempt,
    note: _ErrorNote,
) -> None:
    now = now_utc()
    try:
        await client.create_internal_article(
            attempt.ticket_id,
            f"PDF archiver error ({VERSION})",
            error_note_html(
                ErrorNotePayload(
                    classification=note.classification,
                    message=note.message,
                    action=note.action,
                    request_id=attempt.request_id,
                    delivery_id=attempt.delivery_id,
                    timestamp_utc=format_timestamp_utc(now),
                    code=note.code,
                    hint=note.hint,
                )
            ),
        )
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception(
            "process_ticket.error_note_failed",
            ticket_id=attempt.ticket_id,
            request_id=attempt.request_id,
            delivery_id=attempt.delivery_id,
            classification=note.classification,
        )


async def _apply_error_and_cleanup_processing_tag(
    *,
    client: AsyncZammadClient,
    attempt: workflow.ArchiveAttempt,
    classification_label: str,
    classified: TransientError | PermanentError | None,
    trigger_tag: str,
) -> None:
    try:
        keep_trigger = classified is not None and isinstance(classified, TransientError)
        await async_retry(
            lambda: apply_error(
                client,
                attempt.ticket_id,
                keep_trigger=keep_trigger,
                trigger_tag=trigger_tag,
            ),
            max_retries=1,
            backoff_base=0.3,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception(
            "process_ticket.apply_error_failed",
            ticket_id=attempt.ticket_id,
            request_id=attempt.request_id,
            delivery_id=attempt.delivery_id,
            classification=classification_label,
        )
