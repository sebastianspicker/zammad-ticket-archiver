"""Classify archive failures and project them to Zammad as an error note and error tags."""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from chronikwerk._version import VERSION
from chronikwerk.archiving.error_policy import (
    PERMANENT_ERROR_CODE,
    classify,
    failure_code,
    is_known_failure,
)
from chronikwerk.archiving.notes import (
    ErrorNotePayload,
    action_hint,
    bounded_note_text,
    error_code_and_hint,
    error_note_html,
    preservation_hint,
)
from chronikwerk.archiving.retry import async_retry
from chronikwerk.archiving.tags import apply_error
from chronikwerk.failures import PermanentError, TransientError
from chronikwerk.timestamps import format_timestamp_utc, now_utc
from chronikwerk.zammad.gateway import AsyncZammadClient

log = structlog.get_logger(__name__)


@dataclass(frozen=True)
class FailureNote:
    """Values rendered into one operator-visible archival failure note."""

    classification: str
    message: str
    action: str
    code: str
    hint: str


@dataclass(frozen=True)
class FailureTarget:
    """The ticket and request a failure note and error tags are written for."""

    ticket_id: int
    request_id: str | None
    delivery_id: str | None


@dataclass(frozen=True)
class ClassifiedFailure:
    """A failure with its retry class, journal code, scrubbed message, hint and action."""

    classified: TransientError | PermanentError
    code: str
    message: str
    hint: str
    action: str

    @property
    def transient(self) -> bool:
        """Return whether the failure may succeed when retried."""
        return isinstance(self.classified, TransientError)


def failure_message(exc: BaseException, classified: TransientError | PermanentError) -> str:
    """Return the short reason shown in the note and journaled as ``error_detail``.

    Typed failures and ticket-field errors keep their own text; filesystem, HTTP and database
    errors are reduced to their classified summary; unknown exceptions show only their type.
    Absolute paths and secrets are removed; the full exception text goes to the log only.
    """
    name = exc.__class__.__name__
    return bounded_note_text(f"{name}: {classified}" if is_known_failure(exc) else name)


def classify_failure(exc: BaseException, *, trigger_tag: str) -> ClassifiedFailure:
    """Classify a preservation failure and pick its code, note hint and operator action.

    A preservation code (brief section 5.10) brings its own hint and action; ticket-field and
    path-policy errors keep their established codes; everything else gets a generic code.
    """
    classified = classify(exc)
    code = failure_code(exc, classified)
    action = action_hint(exc, classified=classified, trigger_tag=trigger_tag)
    hint = ""
    if code == PERMANENT_ERROR_CODE:
        code, hint = error_code_and_hint(exc)
    elif (known := preservation_hint(code, trigger_tag=trigger_tag)) is not None:
        hint, action = known
    return ClassifiedFailure(
        classified=classified,
        code=code,
        message=failure_message(exc, classified),
        hint=hint,
        action=action,
    )


async def post_failure_note(
    *,
    client: AsyncZammadClient,
    target: FailureTarget,
    note: FailureNote,
) -> bool:
    """Post the error note; return whether Zammad accepted it (failures are logged)."""
    now = now_utc()
    try:
        await client.create_internal_article(
            target.ticket_id,
            f"PDF archiver error ({VERSION})",
            error_note_html(
                ErrorNotePayload(
                    classification=note.classification,
                    message=note.message,
                    action=note.action,
                    request_id=target.request_id,
                    delivery_id=target.delivery_id,
                    timestamp_utc=format_timestamp_utc(now),
                    code=note.code,
                    hint=note.hint,
                )
            ),
        )
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception(
            "process_ticket.error_note_failed",
            ticket_id=target.ticket_id,
            request_id=target.request_id,
            delivery_id=target.delivery_id,
            classification=note.classification,
        )
        return False
    return True


async def project_final_failure(
    *,
    client: AsyncZammadClient,
    target: FailureTarget,
    note: FailureNote,
    trigger_tag: str,
) -> bool:
    """Set ``pdf:error`` without trigger or ``pdf:processing``, then post the final note.

    Returns whether both reached Zammad. When the tags fail no note is posted, so a later
    report of the same failure does not leave a second note behind.
    """
    try:
        await async_retry(
            lambda: apply_error(
                client,
                target.ticket_id,
                keep_trigger=False,
                trigger_tag=trigger_tag,
            ),
            max_retries=1,
            backoff_base=0.3,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        log.exception(
            "process_ticket.apply_error_failed",
            ticket_id=target.ticket_id,
            request_id=target.request_id,
            delivery_id=target.delivery_id,
            classification=note.classification,
        )
        return False
    return await post_failure_note(client=client, target=target, note=note)
