"""Inject a crash after each commit step, restart, and resume (acceptance check A05)."""

from __future__ import annotations

import asyncio
import stat
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from chronikwerk.archiving.commit import commit_revision
from chronikwerk.archiving.commit_checks import seal_signer_fingerprint
from chronikwerk.archiving.commit_errors import CommitInterruptedError
from chronikwerk.archiving.commit_resume import resume_revision
from chronikwerk.operations.journal import JournalRequest, RequestState
from chronikwerk.preservation.catalog import CatalogRevision
from chronikwerk.preservation.verify import verify_package_directory
from chronikwerk.storage.archive_fs import STAGING_DIR_NAME, STAGING_MARKER_NAME
from tests.support.commit_harness import (
    Harness,
    SimulatedCrashError,
    build_harness,
    restore_modes,
    run_until_crash,
    stored_files,
)
from tests.support.sealed_package import timestamper


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    """Provide a leased request with an assembled package; restore modes for cleanup."""
    built = build_harness(tmp_path)
    yield built
    built.journal.close()
    built.catalog.close()
    restore_modes(built.archive_root)


def _crash_after_step(harness: Harness, monkeypatch: pytest.MonkeyPatch, crash_step: str) -> None:
    """Make the journal raise right after it recorded ``crash_step``."""
    original = harness.journal.record_commit_step

    def record(request_id: str, step: str) -> None:
        """Record the step, then crash when it is the chosen one."""
        original(request_id, step)
        if step == crash_step:
            raise SimulatedCrashError(step)

    monkeypatch.setattr(harness.journal, "record_commit_step", record)


def _commit(harness: Harness) -> Any:
    """Return the commit coroutine for the harness package."""
    return commit_revision(
        harness.assembled, harness.ctx, **harness.commit_kwargs(), timestamper=timestamper()
    )


def _restart(harness: Harness) -> JournalRequest:
    """Restart the process state and return the single VERIFYING request it reports."""
    harness.restart()
    requeued, verifying = harness.journal.recover_after_restart()
    assert requeued == []
    assert [request.request_id for request in verifying] == [harness.ctx.request_id]
    return verifying[0]


def _resume(harness: Harness, request: JournalRequest) -> CatalogRevision | None:
    """Resume the interrupted commit with the harness context."""
    return asyncio.run(
        resume_revision(
            request,
            harness.ctx,
            store=harness.store,
            catalog=harness.catalog,
            journal=harness.journal,
            seal_options=harness.seal_options,
            trust_anchors=harness.anchors,
        )
    )


def _assert_published_and_complete(harness: Harness, revision: CatalogRevision | None) -> None:
    """Check the resumed revision is published read-only, verifiable, cataloged and complete."""
    assert revision is not None
    assert harness.catalog.get(harness.ctx.revision_id) == revision
    published = harness.published_dir
    for path in stored_files(published):
        assert stat.S_IMODE((published / path).stat().st_mode) == 0o440
    report = asyncio.run(
        verify_package_directory(
            published,
            trust_anchors=harness.anchors,
            expected_signer_sha256={seal_signer_fingerprint(harness.seal_options)},
        )
    )
    assert report.passed, report.checks
    request = harness.journal.get(harness.ctx.request_id)
    assert request is not None
    assert (request.state, request.revision_id) == (RequestState.COMPLETE, harness.ctx.revision_id)


def _staging_leftovers(harness: Harness) -> list[str]:
    """Return staged files other than the staging marker."""
    staged = stored_files(harness.archive_root / STAGING_DIR_NAME)
    return [path for path in staged if path != STAGING_MARKER_NAME]


@pytest.mark.parametrize("step", ["sealed", "content_uploaded"])
def test_crash_before_the_seal_is_stored_aborts_on_resume(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    _crash_after_step(harness, monkeypatch, step)
    run_until_crash(_commit(harness))
    request = _restart(harness)
    assert request.commit_step == step
    assert _resume(harness, request) is None
    assert not harness.store.exists(harness.ctx.key_prefix)
    assert _staging_leftovers(harness) == []
    assert harness.catalog.get(harness.ctx.revision_id) is None
    failed = harness.journal.fail_transient(
        request.request_id,
        error_code=CommitInterruptedError.code,
        error_detail="commit interrupted before the seal was stored",
    )
    assert (failed.state, failed.error_code) == (RequestState.WAITING, "commit_interrupted")
    assert failed.revision_id is None


@pytest.mark.parametrize("step", ["seal_uploaded", "read_back", "retention_set", "cataloged"])
def test_crash_after_the_seal_is_stored_completes_on_resume(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    _crash_after_step(harness, monkeypatch, step)
    run_until_crash(_commit(harness))
    request = _restart(harness)
    assert request.commit_step == step
    revision = _resume(harness, request)
    _assert_published_and_complete(harness, revision)
    assert _staging_leftovers(harness) == []


def test_resume_completes_when_only_the_journal_missed_the_catalog_row(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = harness.catalog.add_revision

    def add_then_crash(revision: CatalogRevision) -> None:
        """Insert the catalog row, then crash before the journal hears about it."""
        original(revision)
        raise SimulatedCrashError("after catalog insert")

    monkeypatch.setattr(harness.catalog, "add_revision", add_then_crash)
    run_until_crash(_commit(harness))
    request = _restart(harness)
    assert request.commit_step == "retention_set"
    assert harness.catalog.get(harness.ctx.revision_id) is not None
    _assert_published_and_complete(harness, _resume(harness, request))


def test_resume_refuses_a_context_for_another_revision(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _crash_after_step(harness, monkeypatch, "seal_uploaded")
    run_until_crash(_commit(harness))
    request = _restart(harness)
    harness.ctx = replace(harness.ctx, revision_number=2)
    with pytest.raises(ValueError, match="does not match"):
        _resume(harness, request)
