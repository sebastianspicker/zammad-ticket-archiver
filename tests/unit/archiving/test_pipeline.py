"""Run journaled requests through the preservation pipeline with a real store, journal and seal."""

from __future__ import annotations

import asyncio
import errno
import gc
import json
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

import pytest

from chronikwerk.archiving import pipeline_status
from chronikwerk.archiving.commit_checks import seal_signer_fingerprint
from chronikwerk.archiving.options import ArchiveWorkflowOptions
from chronikwerk.archiving.pipeline import ArchivePipeline
from chronikwerk.archiving.pipeline_options import PreservationOptions
from chronikwerk.configuration.retention import RecordClassRule
from chronikwerk.documents.options import DocumentOptions, SigningOptions, TimestampOptions
from chronikwerk.operations.journal import (
    JournalOptions,
    JournalRequest,
    RequestJournal,
    RequestSource,
    RequestState,
    ZammadStatus,
)
from chronikwerk.preservation.catalog import ArchiveCatalog, CatalogRevision
from chronikwerk.preservation.manifest import MANIFEST_NAME, Producer, parse_manifest
from chronikwerk.preservation.seal import SealOptions
from chronikwerk.preservation.verify import verify_package_directory
from chronikwerk.storage.archive_fs import FilesystemArchiveStore
from chronikwerk.timestamps import add_iso8601_date_duration, now_utc
from chronikwerk.zammad.errors import ServerError
from chronikwerk.zammad.gateway import AsyncZammadClient
from tests.support.commit_harness import restore_modes
from tests.support.fake_capture import CAPTURE_OPTIONS, FakeCaptureClient, fixture_ticket
from tests.support.sealed_package import load_anchors, seal_options, timestamper

TRIGGER = "pdf:sign"
WORKFLOW = ArchiveWorkflowOptions(
    trigger_tag=TRIGGER,
    require_trigger_tag=True,
    acknowledge_on_success=True,
    archive_path_field_name="archive_path",
    archive_user_mode_field_name="archive_user_mode",
    archive_user_field_name="archive_user",
)
DOCUMENTS = DocumentOptions(
    max_articles=0,
    article_limit_mode="render_all",
    locale="en-GB",
    timezone="UTC",
    signing=SigningOptions(
        enabled=False,
        pfx_path=None,
        pfx_password=None,
        reason="Archive",
        location="Test",
        timestamp=TimestampOptions(
            enabled=False,
            tsa_url=None,
            timeout_seconds=5.0,
            ca_bundle_path=None,
            user=None,
            password=None,
            trust_env=False,
            allow_insecure_http=False,
            allow_private_networks=False,
        ),
    ),
)
EXPECTED_PREFIX_START = "it-authorization/alice/Customers/ACME/Ticket-31042/r0001_"


def rule(
    name: str = "it-authorization",
    *,
    retention: str = "P30D",
    trigger: Literal["capture", "case_closed"] = "capture",
) -> RecordClassRule:
    """Return one retention rule for the given record class."""
    return RecordClassRule(name=name, retention=retention, trigger=trigger, readers=("it-leads",))


@dataclass(slots=True)
class PipelineSetup:
    """A pipeline wired to a fake Zammad, a filesystem store and one SQLite database."""

    tmp_path: Path
    client: FakeCaptureClient
    journal: RequestJournal
    catalog: ArchiveCatalog
    store: FilesystemArchiveStore
    seal: SealOptions
    options: PreservationOptions

    @property
    def archive_root(self) -> Path:
        """Return the filesystem store root."""
        return self.tmp_path / "archive"

    @property
    def pipeline(self) -> ArchivePipeline:
        """Return a pipeline over the current services."""
        return ArchivePipeline(
            self.options,
            client=cast(AsyncZammadClient, self.client),
            journal=self.journal,
            catalog=self.catalog,
            store=self.store,
            timestamper=timestamper(),
        )

    def reopen(self) -> None:
        """Reopen journal, catalog and store like a restarted process."""
        self.journal.close()
        self.catalog.close()
        gc.collect()  # releases the writer lock held by a crashed commit's frames
        database = self.tmp_path / "chronikwerk.sqlite3"
        self.journal = RequestJournal(JournalOptions(path=database))
        self.catalog = ArchiveCatalog(database)
        self.store = FilesystemArchiveStore(self.archive_root, fsync=False)

    def close(self) -> None:
        """Close the databases and make published revisions removable again."""
        self.journal.close()
        self.catalog.close()
        restore_modes(self.archive_root)


def build_setup(
    tmp_path: Path,
    client: FakeCaptureClient,
    *,
    rules: tuple[RecordClassRule, ...] = (rule(), rule("general")),
    **option_changes: Any,
) -> PipelineSetup:
    """Wire a pipeline with a synthetic seal identity and trust anchors below ``tmp_path``."""
    keys = tmp_path / "keys"
    keys.mkdir()
    (tmp_path / "work").mkdir()
    seal = seal_options(keys)
    options = PreservationOptions(
        workflow=WORKFLOW,
        documents=DOCUMENTS,
        capture=CAPTURE_OPTIONS,
        seal=seal,
        trust_anchors=load_anchors(keys),
        reading_copies=("pdf/a-2u", "pdf/ua-1"),
        retention_rules={item.name: item for item in rules},
        temp_dir=tmp_path / "work",
        producer=Producer("chronikwerk", "0.3.0a1"),
        source_origin="https://zammad.example.org",
    )
    database = tmp_path / "chronikwerk.sqlite3"
    return PipelineSetup(
        tmp_path=tmp_path,
        client=client,
        journal=RequestJournal(JournalOptions(path=database)),
        catalog=ArchiveCatalog(database),
        store=FilesystemArchiveStore(tmp_path / "archive", fsync=False),
        seal=seal,
        options=replace(options, **option_changes),
    )


def lease(setup: PipelineSetup, source: RequestSource = RequestSource.WEBHOOK) -> JournalRequest:
    """Journal a request for ticket 42 and lease it (COLLECTING)."""
    setup.journal.admit(ticket_id=42, source=source)
    leased = setup.journal.lease_next()
    assert leased is not None
    return leased


def process(setup: PipelineSetup, source: RequestSource = RequestSource.WEBHOOK) -> JournalRequest:
    """Lease and process one request; return its journal row afterwards."""
    request = lease(setup, source)
    asyncio.run(setup.pipeline.process(request))
    row = setup.journal.get(request.request_id)
    assert row is not None
    return row


def revision_of(setup: PipelineSetup, row: JournalRequest) -> CatalogRevision:
    """Return the cataloged revision a complete request points at."""
    assert row.revision_id is not None
    revision = setup.catalog.get(row.revision_id)
    assert revision is not None
    return revision


def assert_verifiable(setup: PipelineSetup, revision: CatalogRevision) -> None:
    """Verify the published package offline with the seal signer pinned."""
    report = asyncio.run(
        verify_package_directory(
            setup.archive_root / revision.key_prefix.rstrip("/"),
            trust_anchors=setup.options.trust_anchors,
            expected_signer_sha256={seal_signer_fingerprint(setup.seal)},
        )
    )
    assert report.passed, report.checks


@pytest.fixture
def setup(tmp_path: Path) -> Iterator[PipelineSetup]:
    """Provide a pipeline for a ticket that carries the trigger tag."""
    built = build_setup(tmp_path, FakeCaptureClient(tags=[TRIGGER]))
    yield built
    built.close()


def test_triggered_request_publishes_a_sealed_revision(setup: PipelineSetup) -> None:
    row = process(setup)

    assert (row.state, row.zammad_status) == (RequestState.COMPLETE, ZammadStatus.DONE)
    revision = revision_of(setup, row)
    assert revision.revision_number == 1
    assert revision.key_prefix.startswith(EXPECTED_PREFIX_START)
    assert setup.store.exists(revision.key_prefix)
    assert_verifiable(setup, revision)
    manifest_path = setup.archive_root / revision.key_prefix / MANIFEST_NAME
    manifest = parse_manifest(manifest_path.read_bytes())
    paths = {file.path for file in manifest.files}
    assert {"reading-copy.pdf", "reading-copy-ua.pdf", "capture.json"} <= paths
    assert manifest.previous_revision_id is None
    assert setup.client.tags == {"pdf:signed"}
    [(subject, body)] = setup.client.notes
    assert "Archive revision complete" in subject
    for value in (
        revision.revision_id,
        revision.manifest_sha256,
        revision.key_prefix,
        "articles 2/2, attachments 1/1",
        "retention_enforced: <code>no</code>",
    ):
        assert value in body
    assert list((setup.tmp_path / "work").iterdir()) == []


def test_ticket_without_trigger_tag_fails_without_touching_it(tmp_path: Path) -> None:
    built = build_setup(tmp_path, FakeCaptureClient(tags=["other"]))
    try:
        row = process(built)
        assert (row.state, row.error_code) == (RequestState.FAILED, "not_triggered")
        assert row.zammad_status is ZammadStatus.DONE
        assert built.client.tag_calls == []
        assert built.client.notes == []
        assert built.client.ticket_calls == 0
    finally:
        built.close()


def test_mail_evidence_request_does_not_need_the_trigger_tag(tmp_path: Path) -> None:
    built = build_setup(tmp_path, FakeCaptureClient(tags=["other"]))
    try:
        row = process(built, RequestSource.MAIL_EVIDENCE)
        assert (row.state, row.zammad_status) == (RequestState.COMPLETE, ZammadStatus.DONE)
        assert built.client.tags == {"other", "pdf:signed"}
    finally:
        built.close()


def test_cleanup_failure_after_completion_keeps_the_complete_revision(
    setup: PipelineSetup, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_cleanup(_self: tempfile.TemporaryDirectory[str]) -> None:
        """Fail like a build area that cannot be removed."""
        raise OSError(errno.EBUSY, "Device or resource busy", "/srv/work/chronikwerk-capture-x")

    monkeypatch.setattr(tempfile.TemporaryDirectory, "cleanup", failing_cleanup)

    row = process(setup)

    assert (row.state, row.zammad_status, row.error_code) == (
        RequestState.COMPLETE,
        ZammadStatus.DONE,
        None,
    )
    assert setup.client.tags == {"pdf:signed"}
    [(subject, body)] = setup.client.notes
    assert "Archive revision complete" in subject
    assert str(row.revision_id) in body


def test_unchanged_capture_completes_after_its_build_area_is_removed(
    setup: PipelineSetup, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = process(setup)
    setup.client.tags.add(TRIGGER)
    work = setup.tmp_path / "work"
    original = setup.journal.complete
    leftovers: list[list[Path]] = []

    def complete(request_id: str, *, revision_id: str) -> None:
        """Remember what the build area holds when the request completes."""
        leftovers.append(list(work.iterdir()))
        original(request_id, revision_id=revision_id)

    monkeypatch.setattr(setup.journal, "complete", complete)

    second = process(setup)

    assert second.revision_id == first.revision_id
    assert leftovers == [[]]


def test_repeated_request_resolves_to_the_same_revision(setup: PipelineSetup) -> None:
    first = process(setup)
    setup.client.tags.add(TRIGGER)

    second = process(setup)

    assert (second.state, second.zammad_status) == (RequestState.COMPLETE, ZammadStatus.DONE)
    assert second.revision_id == first.revision_id
    assert setup.catalog.next_revision_number(42) == 2
    assert setup.client.tags == {"pdf:signed"}
    assert len(setup.client.notes) == 2
    assert str(first.revision_id) in setup.client.notes[1][1]


def test_trigger_set_again_during_a_run_survives_for_the_next_request(tmp_path: Path) -> None:
    def capture_while_the_agent_retriggers(_call: int) -> dict[str, Any]:
        client.tags.add(TRIGGER)
        return fixture_ticket()

    client = FakeCaptureClient(tags=[TRIGGER], tickets=capture_while_the_agent_retriggers)
    built = build_setup(tmp_path, client)
    try:
        process(built)

        assert client.tags == {"pdf:signed", TRIGGER}
        assert process(built).state is RequestState.COMPLETE
    finally:
        built.close()


def test_changed_content_creates_the_next_revision(tmp_path: Path) -> None:
    versions = {"title": "Printer broken"}
    client = FakeCaptureClient(tags=[TRIGGER], tickets=lambda _call: fixture_ticket(**versions))
    built = build_setup(tmp_path, client)
    try:
        first = revision_of(built, process(built))
        versions["title"] = "Printer still broken"
        client.tags.add(TRIGGER)

        second = revision_of(built, process(built))

        assert second.revision_number == 2
        assert second.revision_id != first.revision_id
        assert second.source_digest != first.source_digest
        manifest_path = built.archive_root / second.key_prefix / MANIFEST_NAME
        manifest = parse_manifest(manifest_path.read_bytes())
        assert manifest.previous_revision_id == first.revision_id
        assert_verifiable(built, second)
    finally:
        built.close()


def test_permanent_capture_error_is_noted_tagged_and_journaled(tmp_path: Path) -> None:
    client = FakeCaptureClient(
        tags=[TRIGGER], tickets=lambda _call: fixture_ticket(record_class="unknown-class")
    )
    built = build_setup(tmp_path, client)
    try:
        row = process(built)
        assert (row.state, row.error_code) == (RequestState.FAILED, "record_class_missing")
        assert client.tags == {"pdf:error"}
        [(_, body)] = client.notes
        assert "record_class_missing" in body
        assert "Permanent" in body
        assert not (built.archive_root / "it-authorization").exists()
    finally:
        built.close()


@pytest.mark.parametrize(
    ("failure", "code"),
    [("zammad", "transient_error"), ("temp_space", "temp_space_exceeded")],
)
def test_first_transient_error_backs_off_with_one_retrying_note(
    tmp_path: Path, failure: str, code: str
) -> None:
    client = FakeCaptureClient(tags=[TRIGGER])
    changes: dict[str, Any] = {}
    if failure == "zammad":
        client.fail_ticket_with = ServerError("upstream unavailable")
    else:
        changes["capture"] = replace(CAPTURE_OPTIONS, max_temp_bytes=100)
    built = build_setup(tmp_path, client, **changes)
    try:
        row = process(built)
        assert (row.state, row.error_code, row.attempts) == (RequestState.WAITING, code, 1)
        assert row.next_attempt_at is not None and row.next_attempt_at > now_utc()
        assert client.tags == {"pdf:processing"}
        [(_, body)] = client.notes
        assert code in body
        assert "retrying automatically" in body
    finally:
        built.close()


def test_tag_failure_after_commit_keeps_the_complete_revision(
    setup: PipelineSetup, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline_status, "TAG_BACKOFF_BASE_SECONDS", 0.0)
    setup.client.fail_adding_tag = "pdf:signed"

    row = process(setup)

    assert (row.state, row.zammad_status) == (RequestState.COMPLETE, ZammadStatus.FAILED)
    revision = revision_of(setup, row)
    assert setup.store.exists(revision.key_prefix)
    assert_verifiable(setup, revision)
    assert setup.client.notes == []
    assert setup.journal.pending_zammad_updates()[0].request_id == row.request_id


@pytest.mark.parametrize(
    ("trigger", "close_at", "expected"),
    [
        ("capture", "2026-10-01T12:00:00.000Z", None),
        ("case_closed", "2026-10-01T12:00:00.000Z", datetime(2032, 10, 1, 12, tzinfo=UTC)),
        ("case_closed", None, None),
    ],
)
def test_retain_until_follows_the_record_class_trigger(
    tmp_path: Path,
    trigger: Literal["capture", "case_closed"],
    close_at: str | None,
    expected: datetime | None,
) -> None:
    client = FakeCaptureClient(
        tags=[TRIGGER], tickets=lambda _call: fixture_ticket(close_at=close_at)
    )
    built = build_setup(tmp_path, client, rules=(rule(retention="P6Y", trigger=trigger),))
    try:
        revision = revision_of(built, process(built))
        if expected is None:
            expected = add_iso8601_date_duration(revision.captured_at, "P6Y")
        assert revision.retain_until == expected
        capture = json.loads(
            (built.archive_root / revision.key_prefix / "capture.json").read_bytes()
        )
        assert capture["retention"]["rule"]["trigger"] == trigger
        assert datetime.fromisoformat(capture["retention"]["retain_until"]) == expected
    finally:
        built.close()
