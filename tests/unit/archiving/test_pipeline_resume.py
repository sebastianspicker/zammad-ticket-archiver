"""Resume a pipeline commit that a restart interrupted while its request was VERIFYING."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from chronikwerk.operations.journal import (
    JournalOptions,
    JournalRequest,
    RequestJournal,
    RequestState,
    ZammadStatus,
)
from chronikwerk.preservation.catalog import CatalogRevision
from chronikwerk.timestamps import now_utc
from tests.support.fake_capture import FakeCaptureClient
from tests.unit.archiving.test_pipeline import (
    TRIGGER,
    PipelineSetup,
    assert_verifiable,
    build_setup,
    lease,
    revision_of,
)


class _ProcessCrash(BaseException):  # noqa: N818 - stands in for a process crash, not an error
    """Escapes the pipeline's error handling like a killed process would."""


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[PipelineSetup]:
    """Provide a pipeline for a ticket that carries the trigger tag."""
    built = build_setup(tmp_path, FakeCaptureClient(tags=[TRIGGER]))
    yield built
    built.close()


def _crash_after_step(setup: PipelineSetup, monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    """Make the journal stop the process right after it recorded ``step``."""
    original = setup.journal.record_commit_step

    def record(request_id: str, recorded: str) -> None:
        """Record the step, then crash when it is the chosen one."""
        original(request_id, recorded)
        if recorded == step:
            raise _ProcessCrash(recorded)

    monkeypatch.setattr(setup.journal, "record_commit_step", record)


def _restart(setup: PipelineSetup) -> JournalRequest:
    """Reopen the services and return the single VERIFYING request recovery reports."""
    setup.reopen()
    requeued, verifying = setup.journal.recover_after_restart()
    assert requeued == []
    assert len(verifying) == 1
    return verifying[0]


def _row(setup: PipelineSetup, request_id: str) -> JournalRequest:
    """Return the current journal row of a request."""
    row = setup.journal.get(request_id)
    assert row is not None
    return row


def test_resume_after_crash_at_seal_uploaded_completes_and_reports(
    setup: PipelineSetup, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = lease(setup)
    _crash_after_step(setup, monkeypatch, "seal_uploaded")
    with pytest.raises(_ProcessCrash):
        asyncio.run(setup.pipeline.process(request))
    assert setup.client.notes == []

    interrupted = _restart(setup)
    assert interrupted.commit_step == "seal_uploaded"
    assert interrupted.context_json is not None
    asyncio.run(setup.pipeline.resume(interrupted))

    row = _row(setup, request.request_id)
    assert (row.state, row.zammad_status) == (RequestState.COMPLETE, ZammadStatus.DONE)
    assert row.revision_id == interrupted.revision_id
    revision = revision_of(setup, row)
    assert_verifiable(setup, revision)
    assert setup.client.tags == {"pdf:signed"}
    [(_, body)] = setup.client.notes
    assert revision.revision_id in body


def test_resume_without_context_fails_as_commit_interrupted(setup: PipelineSetup) -> None:
    request = lease(setup)
    setup.journal.mark_verifying(request.request_id, revision_id="rev-1", revision_number=1)
    verifying = _restart(setup)
    assert verifying.context_json is None

    asyncio.run(setup.pipeline.resume(verifying))

    row = _row(setup, request.request_id)
    assert (row.state, row.error_code, row.attempts) == (
        RequestState.WAITING,
        "commit_interrupted",
        2,  # the interrupted attempt and the failed resume both count
    )
    assert row.context_json is None
    # Not the first attempt any more: the retry stays silent and the ticket is unchanged.
    assert setup.client.tags == {TRIGGER}
    assert setup.client.notes == []


def _fail_first_catalog_insert(setup: PipelineSetup, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the first catalog insert fail like a locked database (after publication)."""
    original = setup.catalog.add_revision
    calls: list[str] = []

    def add_revision(revision: CatalogRevision) -> None:
        """Fail the first insert, then insert normally."""
        calls.append(revision.revision_id)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        original(revision)

    monkeypatch.setattr(setup.catalog, "add_revision", add_revision)


def test_failure_after_publication_keeps_the_revision_and_resumes_it(
    setup: PipelineSetup, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_first_catalog_insert(setup, monkeypatch)
    request = lease(setup)
    asyncio.run(setup.pipeline.process(request))

    waiting = _row(setup, request.request_id)
    assert (waiting.state, waiting.attempts, waiting.commit_step) == (
        RequestState.WAITING,
        1,
        "retention_set",
    )
    assert waiting.revision_id is not None and waiting.context_json is not None
    assert setup.client.tags == {"pdf:processing"}
    [(_, retry_note)] = setup.client.notes
    assert "retrying automatically" in retry_note

    # The back-off is due two hours later.
    later = RequestJournal(
        JournalOptions(path=setup.tmp_path / "chronikwerk.sqlite3"),
        clock=lambda: now_utc() + timedelta(hours=2),
    )
    setup.journal.close()
    setup.journal = later
    leased = later.lease_next()
    assert leased is not None and leased.commit_step == "retention_set"
    tickets_fetched = setup.client.ticket_calls
    asyncio.run(setup.pipeline.process(leased))

    row = _row(setup, request.request_id)
    assert (row.state, row.zammad_status) == (RequestState.COMPLETE, ZammadStatus.DONE)
    assert row.revision_id == waiting.revision_id
    assert setup.client.ticket_calls == tickets_fetched  # no second capture
    assert setup.catalog.next_revision_number(42) == 2  # one catalog row
    revision = revision_of(setup, row)
    packages = [path for path in setup.archive_root.rglob("r0*_*") if path.is_dir()]
    assert packages == [setup.archive_root / revision.key_prefix.rstrip("/")]
    assert_verifiable(setup, revision)
    assert setup.client.tags == {"pdf:signed"}
    assert len(setup.client.notes) == 2
    assert revision.revision_id in setup.client.notes[-1][1]
