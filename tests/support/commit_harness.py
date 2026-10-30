"""Wire a real capture, store, journal and catalog together for commit-protocol tests."""

from __future__ import annotations

import asyncio
import gc
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from asn1crypto import x509 as asn1_x509

from chronikwerk.archiving.assemble import AssembledPackage, assemble_package
from chronikwerk.archiving.capture import CapturedSource, capture_ticket
from chronikwerk.archiving.commit import CommitContext
from chronikwerk.archiving.rendering import ReadingCopies
from chronikwerk.documents.models import Snapshot, TicketMeta
from chronikwerk.operations.journal import JournalOptions, RequestJournal, RequestSource
from chronikwerk.preservation.catalog import ArchiveCatalog
from chronikwerk.preservation.digest import policy_id
from chronikwerk.preservation.manifest import Producer
from chronikwerk.preservation.revision import new_revision_id, revision_key_prefix
from chronikwerk.preservation.seal import SealOptions
from chronikwerk.storage.archive_fs import FilesystemArchiveStore
from chronikwerk.timestamps import now_utc
from tests.support.fake_capture import CAPTURE_OPTIONS, FakeCaptureClient
from tests.support.sealed_package import blank_pdf, load_anchors, seal_options

ARCHIVE_USER = "agent"
ARCHIVE_PATH = ("IT", "Access")
PRODUCER = Producer("chronikwerk", "0.3.0a1")
READING_COPY_VARIANTS = ("pdf/a-2u", "pdf/ua-1")
RULE = {"record_class": "it-authorization", "retention": "P30D", "trigger": "capture"}


class SimulatedCrashError(Exception):
    """Stands in for a process crash at a chosen point of the commit."""


def reading_copies(*, accessible: bool = True) -> ReadingCopies:
    """Return small unsigned reading copies built from a placeholder snapshot."""
    snapshot = Snapshot(ticket=TicketMeta(id=42, number="31042", title="Copies"), articles=[])
    return ReadingCopies(
        archival=blank_pdf(),
        accessible=blank_pdf() + b"%ua\n" if accessible else None,
        snapshot=snapshot,
        signing_cert_fingerprint=None,
    )


def capture(tmp_path: Path) -> CapturedSource:
    """Capture fixture ticket 42 with the real clock into ``tmp_path/work``."""
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    return asyncio.run(
        capture_ticket(
            FakeCaptureClient(), ticket_id=42, work_dir=work_dir, options=CAPTURE_OPTIONS
        )
    )


def assemble(captured: CapturedSource, *, revision_id: str, request_id: str) -> AssembledPackage:
    """Assemble revision 1 of the captured ticket with both reading copies."""
    return assemble_package(
        captured,
        reading_copies(),
        revision_id=revision_id,
        revision_number=1,
        previous_revision_id=None,
        policy_id=policy_id(record_class_rule=RULE, reading_copies=READING_COPY_VARIANTS),
        captured_at=captured.collection_finished,
        producer=PRODUCER,
        capture_description={
            "source": {"system": "zammad", "origin": "https://zammad.example.org"},
            "request": {"id": request_id, "source": "webhook"},
            "retention": {"rule": RULE, "retain_until": captured.collection_finished},
            "limits": {"max_attachment_bytes": CAPTURE_OPTIONS.max_attachment_bytes},
        },
    )


@dataclass(slots=True)
class Harness:
    """Everything a commit needs, plus the paths to reopen it after a simulated restart."""

    tmp_path: Path
    store: FilesystemArchiveStore
    journal: RequestJournal
    catalog: ArchiveCatalog
    seal_options: SealOptions
    anchors: tuple[asn1_x509.Certificate, ...]
    assembled: AssembledPackage
    ctx: CommitContext

    @property
    def archive_root(self) -> Path:
        """Return the filesystem store root."""
        return self.tmp_path / "archive"

    @property
    def published_dir(self) -> Path:
        """Return the directory the revision is published to."""
        return self.archive_root / self.ctx.key_prefix.rstrip("/")

    def commit_kwargs(self) -> dict[str, Any]:
        """Return the keyword arguments shared by ``commit_revision`` calls."""
        return {
            "store": self.store,
            "catalog": self.catalog,
            "journal": self.journal,
            "seal_options": self.seal_options,
            "trust_anchors": self.anchors,
        }

    def restart(self) -> None:
        """Drop every open handle and reopen store, journal and catalog like a new process."""
        self.journal.close()
        self.catalog.close()
        gc.collect()  # releases the writer lock held by a crashed commit's frames
        self.store = FilesystemArchiveStore(self.archive_root, fsync=False)
        self.journal = RequestJournal(JournalOptions(path=self.tmp_path / "chronikwerk.sqlite3"))
        self.catalog = ArchiveCatalog(self.tmp_path / "chronikwerk.sqlite3")


def build_harness(tmp_path: Path, *, trusted: bool = True) -> Harness:
    """Capture and assemble ticket 42 and lease its journaled request (COLLECTING)."""
    database = tmp_path / "chronikwerk.sqlite3"
    journal = RequestJournal(JournalOptions(path=database))
    journal.admit(ticket_id=42, source=RequestSource.WEBHOOK, delivery_id="d-1")
    leased = journal.lease_next()
    assert leased is not None
    captured = capture(tmp_path)
    revision_id = new_revision_id()
    keys = tmp_path / "keys"
    keys.mkdir()
    ctx = CommitContext(
        request_id=leased.request_id,
        ticket_id=42,
        ticket_number=captured.ticket.number,
        key_prefix=revision_key_prefix(
            record_class=captured.record_class,
            archive_user=ARCHIVE_USER,
            archive_path=ARCHIVE_PATH,
            ticket_number=captured.ticket.number,
            revision_number=1,
            revision_id=revision_id,
        ),
        revision_id=revision_id,
        revision_number=1,
        record_class=captured.record_class,
        archive_user=ARCHIVE_USER,
        archive_path=ARCHIVE_PATH,
        retain_until=now_utc() + timedelta(days=30),
        collection_started=captured.collection_started,
    )
    return Harness(
        tmp_path=tmp_path,
        store=FilesystemArchiveStore(tmp_path / "archive", fsync=False),
        journal=journal,
        catalog=ArchiveCatalog(database),
        seal_options=seal_options(keys),
        anchors=load_anchors(keys, trusted=trusted),
        assembled=assemble(captured, revision_id=revision_id, request_id=leased.request_id),
        ctx=ctx,
    )


def run_until_crash(coro: Coroutine[Any, Any, Any]) -> None:
    """Run ``coro`` and require it to stop with ``SimulatedCrashError``."""
    try:
        asyncio.run(coro)
    except SimulatedCrashError:
        return
    raise AssertionError("the commit did not reach the injected crash")


def stored_files(root: Path) -> list[str]:
    """List every regular file below ``root`` (published and staged) as relative paths."""
    if not root.exists():
        return []
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def restore_modes(root: Path) -> None:
    """Make published directories writable again so pytest can remove ``tmp_path``."""
    if not root.exists():
        return
    for path in [root, *root.rglob("*")]:
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o700)
