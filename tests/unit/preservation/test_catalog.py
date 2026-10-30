"""Verify the archive catalog: queries, uniqueness, rebuild from the store and journal sharing."""

from __future__ import annotations

import sqlite3
import stat
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from chronikwerk.operations.journal import JournalOptions, RequestJournal, RequestSource
from chronikwerk.preservation.catalog import (
    ArchiveCatalog,
    CatalogError,
    CatalogRevision,
)
from chronikwerk.preservation.manifest import (
    SEAL_NAME,
    TOKEN_NAME,
    Coverage,
    CoverageCount,
    OriginalEmailCoverage,
    Producer,
    TicketRef,
)
from chronikwerk.preservation.package import PackageBuilder
from chronikwerk.preservation.revision import revision_key_prefix
from tests.support.fake_archive_store import FakeArchiveStore

CAPTURED = datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC)
RETAIN = datetime(2036, 10, 14, 10, 0, 0, tzinfo=UTC)


def _revision(
    revision_id: str = "rev-1",
    *,
    ticket_id: int = 42,
    number: int = 1,
    ticket_number: str = "31042",
    record_class: str = "it-authorization",
    captured_at: datetime = CAPTURED,
    source_digest: str = "sha256:s1",
    policy_id: str = "sha256:p1",
) -> CatalogRevision:
    """Build a catalog row with sample values."""
    return CatalogRevision(
        revision_id=revision_id,
        ticket_id=ticket_id,
        ticket_number=ticket_number,
        revision_number=number,
        record_class=record_class,
        archive_user="alice",
        archive_path=("2026", "10"),
        key_prefix=f"{record_class}/alice/2026/10/Ticket-{ticket_number}/r{number:04d}_{revision_id}/",
        captured_at=captured_at,
        source_digest=source_digest,
        policy_id=policy_id,
        manifest_sha256="a" * 64,
        retain_until=RETAIN,
        retention_enforced=True,
        verified_at=None,
        verification_result=None,
    )


@pytest.fixture
def catalog(tmp_path: Path) -> Iterator[ArchiveCatalog]:
    """Open a catalog in a temporary directory."""
    opened = ArchiveCatalog(tmp_path / "state.db")
    yield opened
    opened.close()


def test_add_get_and_roundtrip(catalog: ArchiveCatalog, tmp_path: Path) -> None:
    revision = _revision()
    catalog.add_revision(revision)
    assert catalog.get("rev-1") == revision
    assert catalog.get("missing") is None
    assert stat.S_IMODE((tmp_path / "state.db").stat().st_mode) == 0o600


def test_latest_and_next_revision_number(catalog: ArchiveCatalog) -> None:
    assert catalog.next_revision_number(42) == 1
    assert catalog.latest_for_ticket(42) is None
    catalog.add_revision(_revision("rev-1", number=1))
    catalog.add_revision(_revision("rev-2", number=2))
    catalog.add_revision(_revision("other", ticket_id=7, number=1))
    latest = catalog.latest_for_ticket(42)
    assert latest is not None and latest.revision_id == "rev-2"
    assert catalog.next_revision_number(42) == 3
    assert catalog.next_revision_number(7) == 2


def test_find_matching_uses_digest_and_policy(catalog: ArchiveCatalog) -> None:
    catalog.add_revision(_revision("rev-1", number=1))
    catalog.add_revision(_revision("rev-2", number=2, source_digest="sha256:s2"))
    match = catalog.find_matching(ticket_id=42, source_digest="sha256:s1", policy_id="sha256:p1")
    assert match is not None and match.revision_id == "rev-1"
    assert catalog.find_matching(ticket_id=42, source_digest="sha256:s1", policy_id="x") is None
    assert (
        catalog.find_matching(ticket_id=1, source_digest="sha256:s1", policy_id="sha256:p1") is None
    )


def test_search_filters_and_limit(catalog: ArchiveCatalog) -> None:
    catalog.add_revision(_revision("rev-1", number=1))
    later = CAPTURED + timedelta(days=2)
    catalog.add_revision(_revision("rev-2", number=2, captured_at=later))
    catalog.add_revision(
        _revision("rev-3", ticket_id=9, ticket_number="900", record_class="pilot", number=1)
    )
    assert [r.revision_id for r in catalog.search(ticket_number="31042")] == ["rev-2", "rev-1"]
    assert [r.revision_id for r in catalog.search(record_class="pilot")] == ["rev-3"]
    window = catalog.search(captured_from=CAPTURED + timedelta(days=1), captured_to=later)
    assert [r.revision_id for r in window] == ["rev-2"]
    assert len(catalog.search()) == 3
    assert len(catalog.search(limit=1)) == 1


def test_uniqueness_errors(catalog: ArchiveCatalog) -> None:
    catalog.add_revision(_revision("rev-1", number=1))
    with pytest.raises(CatalogError):
        catalog.add_revision(_revision("rev-1", number=2))
    with pytest.raises(CatalogError):
        catalog.add_revision(_revision("rev-x", number=1))
    assert catalog.next_revision_number(42) == 2


def test_record_verification_and_access(catalog: ArchiveCatalog, tmp_path: Path) -> None:
    catalog.add_revision(_revision())
    checked = CAPTURED + timedelta(days=1)
    catalog.record_verification("rev-1", result="pass", verified_at=checked)
    stored = catalog.get("rev-1")
    assert stored is not None
    assert stored.verified_at == checked and stored.verification_result == "pass"
    with pytest.raises(CatalogError):
        catalog.record_verification("nope", result="pass", verified_at=checked)

    fixed = ArchiveCatalog(tmp_path / "state.db", clock=lambda: CAPTURED)
    fixed.record_access(actor="reader", action="verify", revision_id="rev-1", outcome="ok")
    fixed.close()
    with sqlite3.connect(tmp_path / "state.db") as conn:
        events = conn.execute("SELECT actor, action, revision_id, outcome FROM access_events")
        assert events.fetchall() == [("reader", "verify", "rev-1", "ok")]
        assert conn.execute("SELECT COUNT(*) FROM fixity_checks").fetchone() == (1,)


def test_export_copy_is_readable_and_refuses_existing_target(
    catalog: ArchiveCatalog, tmp_path: Path
) -> None:
    catalog.add_revision(_revision())
    target = tmp_path / "export.db"
    catalog.export_to(target)
    with ArchiveCatalog(target) as copy:
        assert copy.get("rev-1") == _revision()
    with pytest.raises(CatalogError):
        catalog.export_to(target)


def test_closed_catalog_refuses_work(tmp_path: Path) -> None:
    closed = ArchiveCatalog(tmp_path / "state.db")
    closed.close()
    closed.close()
    with pytest.raises(CatalogError):
        closed.get("rev-1")


def test_journal_and_catalog_share_one_file(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with ArchiveCatalog(path) as first:
        first.add_revision(_revision())
    with RequestJournal(JournalOptions(path)) as journal:
        journal.admit(ticket_id=42, source=RequestSource.WEBHOOK, delivery_id="d1")
    with ArchiveCatalog(path) as again:
        assert again.get("rev-1") is not None
    other = tmp_path / "other.db"
    with RequestJournal(JournalOptions(other)) as journal:
        journal.admit(ticket_id=1, source=RequestSource.WEBHOOK, delivery_id="d2")
    with ArchiveCatalog(other) as late:
        late.add_revision(_revision())
    with sqlite3.connect(other) as conn:
        assert conn.execute("PRAGMA user_version").fetchone() == (1,)
        assert conn.execute("SELECT COUNT(*) FROM requests").fetchone() == (1,)


def _publish(
    store: FakeArchiveStore,
    tmp_path: Path,
    *,
    prefix: str,
    manifest: bytes,
    sealed: bool = True,
) -> None:
    """Finalize ``manifest`` (and optionally seal and token) under ``prefix``."""
    writer = store.begin_revision(prefix)
    files = {"manifest.json": manifest}
    if sealed:
        files[SEAL_NAME] = b"seal"
        files[TOKEN_NAME] = b"token"
    for name, data in files.items():
        source = tmp_path / f"src-{abs(hash((prefix, name)))}"
        source.write_bytes(data)
        writer.put_new(name, source, retain_until=RETAIN)
    writer.finalize(retain_until=RETAIN)


def _manifest(
    tmp_path: Path, *, revision_id: str, number: int, ticket_id: int, ticket_number: str
) -> bytes:
    """Build a real manifest for a one-file package with the given identity."""
    build = tmp_path / f"build-{revision_id}"
    build.mkdir()
    builder = PackageBuilder(build)
    return builder.write_manifest(
        revision_id=revision_id,
        revision_number=number,
        previous_revision_id=None,
        ticket=TicketRef(ticket_id, ticket_number),
        captured_at=CAPTURED,
        record_class="it-authorization",
        policy_id="sha256:" + "1" * 64,
        source_digest="sha256:" + "2" * 64,
        coverage=Coverage(
            CoverageCount(0, 0), CoverageCount(0, 0), OriginalEmailCoverage(0, 0), True
        ),
        producer=Producer("chronikwerk", "0"),
    )


def _prefix(revision_id: str, number: int, ticket_number: str) -> str:
    """Return the store prefix of a sample revision."""
    return revision_key_prefix(
        record_class="it-authorization",
        archive_user="alice",
        archive_path=["2026", "10"],
        ticket_number=ticket_number,
        revision_number=number,
        revision_id=revision_id,
    )


def _populated_store(tmp_path: Path) -> FakeArchiveStore:
    """Publish good, unsealed, layout-mismatched and unparsable revisions."""
    store = FakeArchiveStore()
    good = _manifest(tmp_path, revision_id="rid-1", number=1, ticket_id=42, ticket_number="31042")
    _publish(store, tmp_path, prefix=_prefix("rid-1", 1, "31042"), manifest=good)
    good2 = _manifest(tmp_path, revision_id="rid-2", number=2, ticket_id=42, ticket_number="31042")
    _publish(store, tmp_path, prefix=_prefix("rid-2", 2, "31042"), manifest=good2)
    unsealed = _manifest(tmp_path, revision_id="rid-3", number=1, ticket_id=5, ticket_number="5")
    _publish(store, tmp_path, prefix=_prefix("rid-3", 1, "5"), manifest=unsealed, sealed=False)
    disagree = _manifest(tmp_path, revision_id="rid-4", number=1, ticket_id=6, ticket_number="6")
    _publish(store, tmp_path, prefix=_prefix("rid-4", 1, "999"), manifest=disagree)
    _publish(store, tmp_path, prefix=_prefix("rid-5", 1, "7"), manifest=b"not json")
    return store


def test_rebuild_from_store_adds_and_skips_with_reasons(tmp_path: Path) -> None:
    store = _populated_store(tmp_path)
    with ArchiveCatalog(tmp_path / "state.db") as catalog:
        report = catalog.rebuild_from_store(store, record_classes=["it-authorization"])
        assert (report.added, report.updated) == (2, 0)
        reasons = dict(report.skipped)
        assert reasons[_prefix("rid-3", 1, "5")] == "seal or timestamp token missing"
        assert "ticket number" in reasons[_prefix("rid-4", 1, "999")]
        assert "cannot be parsed" in reasons[_prefix("rid-5", 1, "7")]
        assert len(report.skipped) == 3
        stored = catalog.get("rid-2")
        assert stored is not None
        assert stored.ticket_id == 42 and stored.ticket_number == "31042"
        assert stored.archive_user == "alice" and stored.archive_path == ("2026", "10")
        assert stored.retain_until == RETAIN and stored.retention_enforced
        assert stored.key_prefix == _prefix("rid-2", 2, "31042")
        assert len(stored.manifest_sha256) == 64
        assert catalog.next_revision_number(42) == 3


def test_rebuild_updates_keeps_verification_and_reports_missing(tmp_path: Path) -> None:
    store = _populated_store(tmp_path)
    with ArchiveCatalog(tmp_path / "state.db") as catalog:
        catalog.rebuild_from_store(store, record_classes=["it-authorization"])
        catalog.record_verification("rid-1", result="pass", verified_at=CAPTURED)
        catalog.record_access(actor="a", action="x", revision_id="rid-1", outcome="ok")
        ghost = replace(
            _revision("ghost", ticket_id=99, number=1),
            key_prefix="it-authorization/gone/Ticket-1/r0001_ghost/",
        )
        catalog.add_revision(ghost)
        report = catalog.rebuild_from_store(store, record_classes=["it-authorization"])
        assert (report.added, report.updated) == (0, 2)
        assert ("it-authorization/gone/Ticket-1/r0001_ghost/", "missing in store") in report.skipped
        assert catalog.get("ghost") == ghost
        kept = catalog.get("rid-1")
        assert kept is not None and kept.verification_result == "pass"
    with sqlite3.connect(tmp_path / "state.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM access_events").fetchone() == (1,)


def test_rebuild_skips_revision_number_conflict(tmp_path: Path) -> None:
    store = _populated_store(tmp_path)
    with ArchiveCatalog(tmp_path / "state.db") as catalog:
        catalog.add_revision(_revision("other", ticket_id=42, number=1))
        report = catalog.rebuild_from_store(store, record_classes=["it-authorization"])
        assert report.added == 1
        assert any("already cataloged as other" in reason for _, reason in report.skipped)
