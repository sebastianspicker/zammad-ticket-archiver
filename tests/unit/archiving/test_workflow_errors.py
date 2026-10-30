"""Verify classified archive failures and their projection as notes and error tags."""

from __future__ import annotations

import asyncio
import errno
import sqlite3

import pytest

from chronikwerk.archiving.error_policy import ERROR_CODE_CLASSES, classify
from chronikwerk.archiving.pipeline_failures import final_failure_note, retry_failure_note
from chronikwerk.archiving.workflow_errors import (
    ClassifiedFailure,
    FailureTarget,
    classify_failure,
    post_failure_note,
    project_final_failure,
)
from chronikwerk.failures import PermanentError, TransientError
from chronikwerk.zammad.errors import ServerError
from tests.support.fake_zammad import FakeZammad

_TARGET = FailureTarget(ticket_id=42, request_id="request-42", delivery_id="delivery-42")


def _project(fake: FakeZammad, exc: Exception) -> ClassifiedFailure:
    """Classify ``exc`` and project it onto ticket 42 as a final failure, like the pipeline."""
    failure = classify_failure(exc, trigger_tag="archive:ready")
    note = final_failure_note(
        failure, attempts=1 if failure.transient else None, trigger_tag="archive:ready"
    )
    asyncio.run(
        project_final_failure(
            client=fake.client, target=_TARGET, note=note, trigger_tag="archive:ready"
        )
    )
    return failure


@pytest.mark.parametrize(
    ("exc", "transient"),
    [
        (OSError(errno.EAGAIN, "retry later"), True),
        (ValueError("archive_path must not be empty"), False),
    ],
)
def test_final_failure_removes_trigger_and_processing_even_without_a_note(
    exc: Exception, transient: bool
) -> None:
    """A final failure ends with only ``pdf:error``, whatever its retry class."""
    fake = FakeZammad(tags=["pdf:processing", "archive:ready"])
    fake.fail_note = True

    failure = _project(fake, exc)

    assert failure.transient is transient
    assert fake.tags == {"pdf:error"}


def test_final_failure_reports_success_only_when_tags_and_note_arrive() -> None:
    """Failed tags skip the note, so a later report does not leave a second one."""
    failure = classify_failure(ValueError("archive_path must not be empty"), trigger_tag="t")
    note = final_failure_note(failure, attempts=None, trigger_tag="t")

    def project(fake: FakeZammad) -> bool:
        return asyncio.run(
            project_final_failure(client=fake.client, target=_TARGET, note=note, trigger_tag="t")
        )

    def refuse(_tag: str) -> None:
        raise RuntimeError("tags unavailable")

    reachable = FakeZammad(tags=["t"])
    unreachable = FakeZammad(tags=["t"])
    unreachable.on_add_tag = refuse
    without_note = FakeZammad(tags=["t"])
    without_note.fail_note = True

    assert project(reachable) is True
    assert project(unreachable) is False
    assert unreachable.notes == []
    assert project(without_note) is False


def test_failure_note_contains_delivery_id() -> None:
    """The operator note references the delivery that failed."""
    fake = FakeZammad(tags=["archive:ready"])

    _project(fake, OSError(errno.EAGAIN, "retry later"))

    assert len(fake.notes) == 1
    assert "delivery-42" in fake.notes[0][1]
    assert "Failed after 1 attempts" in fake.notes[0][1]
    assert "archive:ready" in fake.notes[0][1]


def test_dual_typed_server_error_note_follows_transient_label() -> None:
    """A retryable Zammad server error is noted as retrying and never gets a permanent code."""
    fake = FakeZammad(tags=["archive:ready"])
    failure = classify_failure(ServerError("upstream unavailable"), trigger_tag="archive:ready")

    posted = asyncio.run(
        post_failure_note(client=fake.client, target=_TARGET, note=retry_failure_note(failure))
    )

    assert posted and failure.transient
    assert "retrying automatically" in fake.notes[0][1]
    assert "permanent_error" not in fake.notes[0][1]
    assert fake.tags == {"archive:ready"}


def test_unknown_exception_is_transient_internal_error_without_its_text() -> None:
    """An exception no rule knows retries with back-off and shows only its type."""
    fake = FakeZammad(tags=["pdf:processing", "archive:ready"])

    failure = _project(fake, RuntimeError("cannot open /var/lib/chronikwerk/state.db"))

    assert failure.transient
    assert failure.code == "internal_error"
    assert ERROR_CODE_CLASSES[failure.code] == "transient"
    assert isinstance(classify(RuntimeError("boom")), TransientError)
    assert failure.message == "RuntimeError"
    assert "/var/lib" not in fake.notes[0][1]
    assert fake.tags == {"pdf:error"}


def test_locked_database_is_transient() -> None:
    failure = classify_failure(
        sqlite3.OperationalError("database is locked"), trigger_tag="archive:ready"
    )

    assert failure.transient
    assert failure.code == "transient_error"


@pytest.mark.parametrize(
    "exc",
    [
        OSError(errno.ENOSPC, "No space left on device", "/srv/work/chronikwerk-capture-x1/a"),
        PermanentError("Seal PFX file not found: /etc/chronikwerk/seal.pfx"),
    ],
)
def test_note_and_journal_detail_omit_absolute_paths(exc: Exception) -> None:
    """The note and the journaled message carry the classified reason, never host paths."""
    fake = FakeZammad(tags=["archive:ready"])

    failure = _project(fake, exc)

    body = fake.notes[0][1]
    for text in (failure.message, body):
        assert "/srv/work" not in text and "/etc/chronikwerk" not in text
    assert failure.code in body
    if isinstance(exc, OSError):
        assert failure.message == f"OSError: Temporary filesystem error (errno={errno.ENOSPC})"
    else:
        assert failure.message == "PermanentError: Seal PFX file not found: <path>"
