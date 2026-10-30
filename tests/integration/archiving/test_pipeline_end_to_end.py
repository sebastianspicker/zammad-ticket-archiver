"""Run webhooks through the journaled scheduler and the real pipeline, renderer and store."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from chronikwerk.archiving.options import ArchiveWorkflowOptions
from chronikwerk.archiving.pipeline import ArchivePipeline
from chronikwerk.archiving.pipeline_options import PreservationOptions
from chronikwerk.configuration.retention import RecordClassRule
from chronikwerk.documents.options import DocumentOptions, SigningOptions, TimestampOptions
from chronikwerk.operations.job import TicketJob
from chronikwerk.operations.journal import (
    JournalOptions,
    JournalRequest,
    RequestJournal,
    RequestState,
)
from chronikwerk.operations.processing import RequestProcessor
from chronikwerk.operations.scheduling import JournaledSchedulingService
from chronikwerk.preservation.catalog import ArchiveCatalog
from chronikwerk.preservation.manifest import Producer
from chronikwerk.storage.archive_fs import FilesystemArchiveStore
from chronikwerk.zammad.gateway import AsyncZammadClient
from tests.support.commit_harness import restore_modes
from tests.support.fake_capture import CAPTURE_OPTIONS, FakeCaptureClient
from tests.support.sealed_package import load_anchors, seal_options, timestamper

TRIGGER = "pdf:sign"
_WORKFLOW = ArchiveWorkflowOptions(
    trigger_tag=TRIGGER,
    require_trigger_tag=True,
    acknowledge_on_success=True,
    archive_path_field_name="archive_path",
    archive_user_mode_field_name="archive_user_mode",
    archive_user_field_name="archive_user",
)
_DOCUMENTS = DocumentOptions(
    max_articles=250,
    article_limit_mode="fail",
    locale="en-GB",
    timezone="UTC",
    signing=SigningOptions(
        enabled=False,
        pfx_path=None,
        pfx_password=None,
        reason="r",
        location="l",
        timestamp=TimestampOptions(
            enabled=False,
            tsa_url=None,
            timeout_seconds=1.0,
            ca_bundle_path=None,
            user=None,
            password=None,
            trust_env=False,
            allow_insecure_http=False,
            allow_private_networks=False,
        ),
    ),
)
_RULE = RecordClassRule(
    name="it-authorization", retention="P30D", trigger="capture", readers=("it-leads",)
)


class _Runtime:
    """One journal, catalog, store and pipeline below ``tmp_path``, like the composition."""

    def __init__(
        self, tmp_path: Path, client: FakeCaptureClient, *, acknowledge_on_success: bool
    ) -> None:
        keys = tmp_path / "keys"
        keys.mkdir()
        (tmp_path / "work").mkdir()
        database = tmp_path / "chronikwerk.sqlite3"
        self.archive_root = tmp_path / "archive"
        self.client = client
        self.journal = RequestJournal(JournalOptions(path=database))
        self.catalog = ArchiveCatalog(database)
        options = PreservationOptions(
            workflow=replace(_WORKFLOW, acknowledge_on_success=acknowledge_on_success),
            documents=_DOCUMENTS,
            capture=CAPTURE_OPTIONS,
            seal=seal_options(keys),
            trust_anchors=load_anchors(keys),
            reading_copies=("pdf/a-2u", "pdf/ua-1"),
            retention_rules={_RULE.name: _RULE},
            temp_dir=tmp_path / "work",
            producer=Producer("chronikwerk", "0.3.0a1"),
            source_origin="https://zammad.example.org",
        )
        pipeline = ArchivePipeline(
            options,
            client=cast(AsyncZammadClient, client),
            journal=self.journal,
            catalog=self.catalog,
            store=FilesystemArchiveStore(self.archive_root, fsync=False),
            timestamper=timestamper(),
        )
        self.scheduler = JournaledSchedulingService(
            journal=self.journal,
            # Until ArchivePipeline implements the maintenance methods of RequestProcessor.
            processor=cast(RequestProcessor, pipeline),
            max_pending=10,
            max_running=2,
            shutdown_timeout_seconds=60.0,
            poll_interval_seconds=0.05,
        )

    def rows(self) -> list[JournalRequest]:
        """Return every journaled request, oldest first."""
        return sorted(self.journal.list_recent(), key=lambda row: row.received_at)

    def close(self) -> None:
        """Close the databases and make published revisions removable again."""
        self.journal.close()
        self.catalog.close()
        restore_modes(self.archive_root)


@pytest.fixture
def runtime_factory(tmp_path: Path) -> Iterator[Callable[..., _Runtime]]:
    """Build at most one runtime per test and close it afterwards."""
    built: list[_Runtime] = []

    def factory(*, acknowledge_on_success: bool = True) -> _Runtime:
        client = FakeCaptureClient(tags=[TRIGGER])
        built.append(_Runtime(tmp_path, client, acknowledge_on_success=acknowledge_on_success))
        return built[-1]

    yield factory
    for runtime in built:
        runtime.close()


async def _until_settled(runtime: _Runtime, count: int, timeout: float = 60.0) -> None:
    """Wait until ``count`` requests exist and none is waiting or in flight."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    terminal = {RequestState.COMPLETE, RequestState.FAILED}
    while True:
        rows = runtime.rows()
        if len(rows) == count and all(row.state in terminal for row in rows):
            return
        if loop.time() > deadline:
            raise AssertionError(f"requests did not settle: {rows}")
        await asyncio.sleep(0.05)


def test_repeated_delivery_publishes_one_sealed_revision(
    runtime_factory: Callable[..., _Runtime],
) -> None:
    runtime = runtime_factory()

    async def scenario() -> None:
        await runtime.scheduler.start()
        assert await runtime.scheduler.schedule(TicketJob(ticket_id=42, delivery_id="d-1"))
        assert await runtime.scheduler.schedule(TicketJob(ticket_id=42, delivery_id="d-1"))
        await _until_settled(runtime, 1)
        await runtime.scheduler.aclose()

    asyncio.run(scenario())

    [row] = runtime.rows()
    assert row.state is RequestState.COMPLETE
    assert row.revision_id is not None
    revision = runtime.catalog.get(row.revision_id)
    assert revision is not None
    assert (runtime.archive_root / revision.key_prefix / "manifest.json.p7s.tst").is_file()
    assert runtime.client.tags == {"pdf:signed"}
    [(_subject, body)] = runtime.client.notes
    assert row.revision_id in body


def test_unchanged_ticket_triggered_again_resolves_to_the_same_revision(
    runtime_factory: Callable[..., _Runtime],
) -> None:
    """Acceptance check A04 through the scheduler: no second revision for the same content."""
    runtime = runtime_factory(acknowledge_on_success=False)

    async def scenario() -> None:
        await runtime.scheduler.start()
        assert await runtime.scheduler.schedule(TicketJob(ticket_id=42, delivery_id="d-1"))
        await _until_settled(runtime, 1)
        runtime.client.tags.add(TRIGGER)
        assert await runtime.scheduler.schedule(TicketJob(ticket_id=42, delivery_id="d-2"))
        await _until_settled(runtime, 2)
        await runtime.scheduler.aclose()

    asyncio.run(scenario())

    first, second = runtime.rows()
    assert (first.state, second.state) == (RequestState.COMPLETE, RequestState.COMPLETE)
    assert first.revision_id == second.revision_id
    assert runtime.catalog.next_revision_number(42) == 2
    assert runtime.client.notes == []
    assert runtime.client.tags == {"pdf:signed"}
