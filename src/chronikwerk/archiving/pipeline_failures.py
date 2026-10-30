"""Decide which failure note a request gets: one per retry series and one when it ends.

Contract: preservation brief (ADRs 0011-0013) section 5.10.

* The first transient failure of a request (no earlier attempt) posts one note saying it is
  retried automatically; the ticket keeps ``pdf:processing`` and gets no ``pdf:error``.
* Later transient failures that still retry change nothing on the ticket.
* A permanent failure, or a transient one that exhausts the retries, posts the final note,
  sets ``pdf:error`` and removes the trigger tag and ``pdf:processing``.

``journaled_failure_note`` renders the failures the scheduler or a restart recorded in the
journal (``RequestProcessor.report_failure``); those rows carry only code and detail.
``journal_failure`` records a pipeline failure and marks it reported when the final note
reached Zammad.
"""

from __future__ import annotations

from dataclasses import dataclass

from chronikwerk.archiving.error_policy import INTERNAL_ERROR_CODE
from chronikwerk.archiving.notes import bounded_note_text, preservation_hint, ticket_field_hint
from chronikwerk.archiving.workflow_errors import ClassifiedFailure, FailureNote
from chronikwerk.operations.journal import (
    JournalRequest,
    RequestJournal,
    RequestState,
    ZammadStatus,
)

__all__ = [
    "FailureOutcome",
    "final_failure_note",
    "journal_failure",
    "journaled_failure_note",
    "retry_failure_note",
]

RETRYING_LABEL = "Temporary failure, retrying automatically"
PERMANENT_LABEL = "Permanent"


@dataclass(frozen=True, slots=True)
class FailureOutcome:
    """How ``journal_failure`` records one failure of the pipeline."""

    transient: bool
    keep_revision: bool
    reported: bool  # the final note and ``pdf:error`` already reached Zammad


def journal_failure(
    journal: RequestJournal,
    request_id: str,
    failure: ClassifiedFailure,
    outcome: FailureOutcome,
) -> None:
    """Journal the failure; a FAILED row whose final report reached Zammad is DONE (blocking).

    Any other FAILED row keeps Zammad status PENDING, so maintenance reports it later.
    """
    if outcome.transient:
        state = journal.fail_transient(
            request_id,
            error_code=failure.code,
            error_detail=failure.message,
            keep_revision=outcome.keep_revision,
        ).state
    else:
        journal.fail_permanent(request_id, error_code=failure.code, error_detail=failure.message)
        state = RequestState.FAILED
    if outcome.reported and state is RequestState.FAILED:
        journal.set_zammad_status(request_id, ZammadStatus.DONE)


def retry_failure_note(failure: ClassifiedFailure) -> FailureNote:
    """Return the single note of a transient failure that is retried automatically."""
    return FailureNote(
        classification=RETRYING_LABEL,
        message=failure.message,
        action=failure.action,
        code=failure.code,
        hint=failure.hint,
    )


def _exhausted_action(trigger_tag: str) -> str:
    return f"Automatic retries are exhausted. Fix the cause, then reapply {trigger_tag}."


def final_failure_note(
    failure: ClassifiedFailure, *, attempts: int | None, trigger_tag: str
) -> FailureNote:
    """Return the final note; ``attempts`` is set when the retries are exhausted."""
    if attempts is None:
        return FailureNote(
            classification=PERMANENT_LABEL,
            message=failure.message,
            action=failure.action,
            code=failure.code,
            hint=failure.hint,
        )
    return FailureNote(
        classification=f"Failed after {attempts} attempts",
        message=failure.message,
        action=_exhausted_action(trigger_tag),
        code=failure.code,
        hint=failure.hint,
    )


def journaled_failure_note(
    request: JournalRequest, *, retry_max_attempts: int, trigger_tag: str
) -> FailureNote:
    """Return the final note of a FAILED row from its journaled code and detail."""
    code = request.error_code or INTERNAL_ERROR_CODE
    known = preservation_hint(code, trigger_tag=trigger_tag)
    hint, action = known or (
        ticket_field_hint(code),
        f"Fix the underlying issue, then reapply {trigger_tag}.",
    )
    classification = PERMANENT_LABEL
    if request.attempts > retry_max_attempts:
        classification = f"Failed after {request.attempts} attempts"
        action = _exhausted_action(trigger_tag)
    return FailureNote(
        classification=classification,
        message=bounded_note_text(request.error_detail or code),
        action=action,
        code=code,
        hint=hint,
    )
