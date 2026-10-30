"""Pin the commit protocol: pre-completion checks, store writes, read-back and cataloging."""

from __future__ import annotations

import asyncio
import io
import stat
import time
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any, BinaryIO

import httpx
import pytest

from chronikwerk.archiving.capture_errors import (
    AttachmentTooLargeError,
    InconsistentSourceError,
    RecordClassMissingError,
    TempSpaceExceededError,
)
from chronikwerk.archiving.commit import commit_revision
from chronikwerk.archiving.commit_checks import seal_signer_fingerprint
from chronikwerk.archiving.commit_errors import (
    CommitInterruptedError,
    SealCheckFailedError,
    StoreConflictError,
    StoreWriteFailedError,
    TimestampCheckFailedError,
    TimestampUnavailableError,
    VerificationFailedError,
)
from chronikwerk.archiving.error_policy import ERROR_CODE_CLASSES, classify, error_code
from chronikwerk.failures import PermanentError, TransientError
from chronikwerk.operations.journal import RequestState
from chronikwerk.preservation.catalog import CatalogRevision
from chronikwerk.preservation.package import PackageTooLargeError
from chronikwerk.preservation.verify import verify_package_directory
from chronikwerk.storage.archive_fs import STAGING_DIR_NAME, STAGING_MARKER_NAME
from chronikwerk.timestamps import now_utc
from tests.support.commit_harness import Harness, build_harness, restore_modes, stored_files
from tests.support.sealed_package import timestamper


@pytest.fixture
def harness(tmp_path: Path) -> Iterator[Harness]:
    """Provide a leased request with an assembled package; restore modes for cleanup."""
    built = build_harness(tmp_path)
    yield built
    built.journal.close()
    built.catalog.close()
    restore_modes(built.archive_root)


def _commit(harness: Harness, **overrides: Any) -> CatalogRevision:
    """Commit the harness package with the synthetic TSA unless overridden."""
    kwargs = {**harness.commit_kwargs(), "timestamper": timestamper(), **overrides}
    return asyncio.run(commit_revision(harness.assembled, harness.ctx, **kwargs))


def _staging_leftovers(harness: Harness) -> list[str]:
    """Return staged files other than the staging marker."""
    staged = stored_files(harness.archive_root / STAGING_DIR_NAME)
    return [path for path in staged if path != STAGING_MARKER_NAME]


def _writer_class(harness: Harness) -> type[Any]:
    """Return the store's writer class, discarding the probe revision used to find it."""
    probe = harness.store.begin_revision("probe/")
    probe.abort()
    return type(probe)


def test_commit_publishes_a_verifiable_revision(harness: Harness) -> None:
    revision = _commit(harness)
    assert harness.store.exists(harness.ctx.key_prefix)
    published = harness.published_dir
    expected = {file.path for file in harness.assembled.manifest.files} | {
        "manifest.json",
        "manifest.json.p7s",
        "manifest.json.p7s.tst",
    }
    assert set(stored_files(published)) == expected
    for path in expected:
        assert stat.S_IMODE((published / path).stat().st_mode) == 0o440
    assert _staging_leftovers(harness) == []
    fingerprint = seal_signer_fingerprint(harness.seal_options)
    report = asyncio.run(
        verify_package_directory(
            published, trust_anchors=harness.anchors, expected_signer_sha256={fingerprint}
        )
    )
    assert report.passed, report.checks
    assert harness.catalog.get(harness.ctx.revision_id) == revision
    assert revision.key_prefix == harness.ctx.key_prefix
    assert revision.archive_path == ("IT", "Access")
    assert revision.verification_result == "passed"
    request = harness.journal.get(harness.ctx.request_id)
    assert request is not None
    assert request.state is RequestState.COMPLETE
    assert (request.revision_id, request.commit_step) == (harness.ctx.revision_id, "cataloged")


def test_commit_records_each_step_in_order(harness: Harness, monkeypatch) -> None:
    steps: list[str] = []
    original = harness.journal.record_commit_step

    def record(request_id: str, step: str) -> None:
        """Record the step and remember its name."""
        original(request_id, step)
        steps.append(str(step))

    monkeypatch.setattr(harness.journal, "record_commit_step", record)
    _commit(harness)
    assert steps == [
        "sealed",
        "content_uploaded",
        "seal_uploaded",
        "read_back",
        "retention_set",
        "cataloged",
    ]


def test_untrusted_seal_fails_permanently_and_writes_nothing(tmp_path: Path) -> None:
    harness = build_harness(tmp_path, trusted=False)
    with pytest.raises(SealCheckFailedError) as caught:
        _commit(harness)
    assert caught.value.code == "seal_failed"
    assert isinstance(classify(caught.value), PermanentError)
    assert stored_files(harness.archive_root) == []
    assert (harness.assembled.build_dir / "manifest.json.p7s").is_file()
    request = harness.journal.get(harness.ctx.request_id)
    assert request is not None
    assert (request.state, request.commit_step) == (RequestState.VERIFYING, "sealed")


class _UnreachableTsa:
    """Timestamper whose TSA connection always fails."""

    async def async_timestamp(self, message_digest: bytes, md_algorithm: str) -> Any:
        """Fail like an unreachable TSA host."""
        raise httpx.ConnectError("TSA down")


def test_tsa_outage_is_transient_and_writes_nothing(harness: Harness) -> None:
    with pytest.raises(TimestampUnavailableError) as caught:
        _commit(harness, timestamper=_UnreachableTsa())
    assert caught.value.code == "timestamp_failed"
    assert isinstance(classify(caught.value), TransientError)
    assert stored_files(harness.archive_root) == []


def test_token_outside_the_collection_window_is_rejected(harness: Harness) -> None:
    harness.ctx = replace(harness.ctx, collection_started=now_utc() + timedelta(hours=1))
    with pytest.raises(TimestampCheckFailedError, match="collection window"):
        _commit(harness)
    assert stored_files(harness.archive_root) == []


def test_read_back_mismatch_aborts_without_publishing(harness: Harness, monkeypatch) -> None:
    writer_class = _writer_class(harness)
    original = writer_class.open

    def altered_open(self: Any, relative_key: str) -> BinaryIO:
        """Return altered bytes for the ticket body, the stored bytes otherwise."""
        if relative_key == "source/ticket.json":
            return io.BytesIO(b'{"tampered": true}')
        return original(self, relative_key)

    monkeypatch.setattr(writer_class, "open", altered_open)
    with pytest.raises(VerificationFailedError) as caught:
        _commit(harness)
    assert caught.value.code == "verification_failed"
    assert not harness.store.exists(harness.ctx.key_prefix)
    assert _staging_leftovers(harness) == []
    assert harness.catalog.get(harness.ctx.revision_id) is None


def test_store_failure_is_transient_and_aborts_the_staged_revision(
    harness: Harness, monkeypatch
) -> None:
    writer_class = _writer_class(harness)
    original = writer_class.put_new

    def failing_put(self: Any, relative_key: str, source: Path, **kwargs: Any) -> Any:
        """Fail when the seal is written, after the content went in."""
        if relative_key == "manifest.json.p7s":
            raise OSError(5, "I/O error")
        return original(self, relative_key, source, **kwargs)

    monkeypatch.setattr(writer_class, "put_new", failing_put)
    with pytest.raises(StoreWriteFailedError) as caught:
        _commit(harness)
    assert caught.value.code == "store_write_failed"
    assert isinstance(classify(caught.value), TransientError)
    assert not harness.store.exists(harness.ctx.key_prefix)
    assert _staging_leftovers(harness) == []


def test_store_os_error_message_names_the_errno_but_no_host_path(
    harness: Harness, monkeypatch
) -> None:
    writer_class = _writer_class(harness)
    secret_path = "/srv/archive/.staging/3f2a9c/source/ticket.json"

    def failing_put(self: Any, relative_key: str, source: Path, **kwargs: Any) -> Any:
        """Fail like a full disk, naming the absolute staging path."""
        raise OSError(28, "No space left on device", secret_path)

    monkeypatch.setattr(writer_class, "put_new", failing_put)
    with pytest.raises(StoreWriteFailedError) as caught:
        _commit(harness)
    message = str(caught.value)
    assert "errno=28" in message
    assert "/srv/archive" not in message and "3f2a9c" not in message
    assert caught.value.__cause__ is not None and secret_path in str(caught.value.__cause__)


def test_commit_keeps_the_event_loop_responsive_while_the_store_blocks(
    harness: Harness, monkeypatch
) -> None:
    writer_class = _writer_class(harness)
    original = writer_class.put_new
    blocked: list[tuple[float, float]] = []

    def slow_put(self: Any, relative_key: str, source: Path, **kwargs: Any) -> Any:
        """Block the calling thread like a slow network share and remember for how long."""
        started = time.monotonic()
        time.sleep(0.1)
        blocked.append((started, time.monotonic()))
        return original(self, relative_key, source, **kwargs)

    monkeypatch.setattr(writer_class, "put_new", slow_put)

    async def scenario() -> list[float]:
        ticks: list[float] = []

        async def ticker() -> None:
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.005)

        task = asyncio.create_task(ticker())
        try:
            await commit_revision(
                harness.assembled,
                harness.ctx,
                **harness.commit_kwargs(),
                timestamper=timestamper(),
            )
        finally:
            task.cancel()
        return ticks

    ticks = asyncio.run(scenario())
    assert len(blocked) == len(harness.assembled.manifest.files) + 3
    for started, finished in blocked:
        assert sum(started < tick < finished for tick in ticks) >= 5


def test_existing_published_prefix_is_a_store_conflict(harness: Harness) -> None:
    other = harness.store.begin_revision(harness.ctx.key_prefix)
    source = harness.tmp_path / "other.txt"
    source.write_bytes(b"other")
    other.put_new("other.txt", source, retain_until=harness.ctx.retain_until)
    other.finalize(retain_until=harness.ctx.retain_until)
    with pytest.raises(StoreConflictError) as caught:
        _commit(harness)
    assert caught.value.code == "store_conflict"


_CODED_ERRORS = (
    RecordClassMissingError,
    InconsistentSourceError,
    AttachmentTooLargeError,
    PackageTooLargeError,
    TempSpaceExceededError,
    SealCheckFailedError,
    TimestampUnavailableError,
    StoreWriteFailedError,
    StoreConflictError,
    VerificationFailedError,
    CommitInterruptedError,
)


@pytest.mark.parametrize("error_class", _CODED_ERRORS)
def test_error_classes_keep_their_code_and_retry_class(error_class: type[Exception]) -> None:
    error = error_class("detail")
    code = error_code(error)
    assert code is not None
    expected = TransientError if ERROR_CODE_CLASSES[code] == "transient" else PermanentError
    assert classify(error) is error
    assert isinstance(error, expected)


def test_invalid_timestamp_token_is_the_permanent_variant_of_its_code() -> None:
    error = TimestampCheckFailedError("bad token")
    assert error_code(error) == "timestamp_failed"
    assert isinstance(classify(error), PermanentError)
    assert error_code(ValueError("plain")) is None
