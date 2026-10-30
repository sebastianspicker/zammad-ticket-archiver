"""Verify the in-memory fake archive store models retention semantics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from chronikwerk.storage.archive_store import (
    ArchiveStoreError,
    ObjectExistsError,
    RevisionExistsError,
)
from tests.support.fake_archive_store import FakeArchiveStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)
RETAIN = NOW + timedelta(days=30)
PREFIX = "tickets/1/rev-1/"
KEY = f"{PREFIX}doc.pdf"


def _published(tmp_path: Path) -> FakeArchiveStore:
    """Return a store holding one finalized revision."""
    store = FakeArchiveStore()
    writer = store.begin_revision(PREFIX)
    source = tmp_path / "doc.pdf"
    source.write_bytes(b"pdf")
    writer.put_new("doc.pdf", source, retain_until=RETAIN)
    assert not store.exists(PREFIX)
    writer.finalize(retain_until=RETAIN)
    return store


def test_fake_store_follows_the_store_protocol(tmp_path: Path) -> None:
    store = _published(tmp_path)

    assert store.retention_enforced is True
    assert store.exists(PREFIX)
    assert list(store.list("tickets/")) == [KEY]
    with store.open(KEY) as handle:
        assert handle.read() == b"pdf"
    info = store.head(KEY)
    assert (info.size, info.retain_until, info.legal_hold) == (3, RETAIN, False)
    with pytest.raises(RevisionExistsError):
        store.begin_revision(PREFIX)
    resumed = store.begin_revision(PREFIX, resume=True)
    assert resumed.keys() == ["doc.pdf"]


def test_fake_objects_are_create_only(tmp_path: Path) -> None:
    store = FakeArchiveStore()
    writer = store.begin_revision(PREFIX)
    one = tmp_path / "one"
    one.write_bytes(b"1")
    two = tmp_path / "two"
    two.write_bytes(b"2")
    first = writer.put_new("x", one, retain_until=RETAIN)

    assert writer.put_new("x", one, retain_until=RETAIN) == first
    with pytest.raises(ObjectExistsError):
        writer.put_new("x", two, retain_until=RETAIN)
    writer.abort()
    assert not store.exists(PREFIX)


def test_retention_can_extend_but_not_shorten(tmp_path: Path) -> None:
    store = _published(tmp_path)

    store.extend_retention(KEY, RETAIN + timedelta(days=1))
    with pytest.raises(ArchiveStoreError):
        store.extend_retention(KEY, RETAIN)
    assert store.head(KEY).retain_until == RETAIN + timedelta(days=1)


def test_delete_refused_while_retained_or_held(tmp_path: Path) -> None:
    store = _published(tmp_path)

    with pytest.raises(ArchiveStoreError):
        store.delete(KEY, now=NOW)
    store.set_legal_hold(KEY, True)
    with pytest.raises(ArchiveStoreError):
        store.delete(KEY, now=RETAIN + timedelta(days=1))
    store.set_legal_hold(KEY, False)
    store.delete(KEY, now=RETAIN + timedelta(days=1))
    with pytest.raises(FileNotFoundError):
        store.open(KEY)
