"""Verify the filesystem archive store is create-only, atomic and confined."""

from __future__ import annotations

import errno
import hashlib
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from chronikwerk.storage import archive_fs
from chronikwerk.storage.archive_fs import (
    LOCK_SUFFIX,
    STAGING_DIR_NAME,
    STAGING_MARKER_NAME,
    FilesystemArchiveStore,
)
from chronikwerk.storage.archive_store import (
    ArchiveStore,
    ArchiveStoreError,
    ObjectExistsError,
    RevisionExistsError,
    RevisionWriter,
)
from tests.support.fake_archive_store import FakeArchiveStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)
RETAIN = NOW + timedelta(days=3650)
LONG_AGO = NOW - timedelta(days=30)
RECENT = NOW - timedelta(minutes=1)
PREFIX = "tickets/42/rev-1/"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[FilesystemArchiveStore]:
    """Provide a store under tmp_path and restore directory modes for cleanup."""
    root = tmp_path / "archive"
    yield FilesystemArchiveStore(root, fsync=False)
    for path in [root, *root.rglob("*")]:
        if path.is_dir() and not path.is_symlink():
            path.chmod(0o700)


def _source(tmp_path: Path, name: str, data: bytes) -> Path:
    """Write a source file and return its path."""
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _staged(store: FilesystemArchiveStore, tmp_path: Path, prefix: str = PREFIX) -> RevisionWriter:
    """Stage two objects under ``prefix`` and return the writer."""
    writer = store.begin_revision(prefix)
    writer.put_new("a/doc.pdf", _source(tmp_path, "doc.pdf", b"pdf"), retain_until=RETAIN)
    writer.put_new("meta.json", _source(tmp_path, "meta.json", b"{}"), retain_until=RETAIN)
    return writer


def _staging_dir(store: FilesystemArchiveStore, prefix: str = PREFIX) -> Path:
    """Return the staging directory used for ``prefix``."""
    return store._layout.staging_root / hashlib.sha256(prefix.encode()).hexdigest()[:32]


def _lock_of(staging: Path) -> Path:
    """Return the lock file guarding a staging directory."""
    return staging.with_name(f"{staging.name}{LOCK_SUFFIX}")


def _age(staging: Path, when: datetime) -> None:
    """Set the mtime of a staging tree and its lock file without following symlinks."""
    stamp = when.timestamp()
    for path in [staging, *staging.rglob("*"), _lock_of(staging)]:
        if os.path.lexists(path):
            os.utime(path, (stamp, stamp), follow_symlinks=False)


def _assert_read_only(final: Path) -> None:
    """Assert the published modes: files 0o440, directories 0o550."""
    assert final.stat().st_mode & 0o777 == 0o550
    assert (final / "a").stat().st_mode & 0o777 == 0o550
    assert (final / "meta.json").stat().st_mode & 0o777 == 0o440
    assert (final / "a" / "doc.pdf").stat().st_mode & 0o777 == 0o440


def test_put_read_back_and_finalize(store: FilesystemArchiveStore, tmp_path: Path) -> None:
    writer = _staged(store, tmp_path)

    assert writer.keys() == ["a/doc.pdf", "meta.json"]
    with writer.open("a/doc.pdf") as handle:
        assert handle.read() == b"pdf"
    assert not store.exists(PREFIX)

    writer.finalize(retain_until=RETAIN)

    assert store.exists(PREFIX)
    with store.open(f"{PREFIX}a/doc.pdf") as handle:
        assert handle.read() == b"pdf"
    staged = store._layout.staging_root / hashlib.sha256(PREFIX.encode()).hexdigest()[:32]
    assert not staged.exists()


def test_put_new_is_create_only_and_idempotent(
    store: FilesystemArchiveStore, tmp_path: Path
) -> None:
    writer = store.begin_revision(PREFIX)
    first = writer.put_new("x.bin", _source(tmp_path, "x1", b"one"), retain_until=RETAIN)
    again = writer.put_new("x.bin", _source(tmp_path, "x2", b"one"), retain_until=RETAIN)

    assert first == again
    assert first.sha256 == hashlib.sha256(b"one").hexdigest()
    assert first.size == 3
    assert first.key == f"{PREFIX}x.bin"
    with pytest.raises(ObjectExistsError):
        writer.put_new("x.bin", _source(tmp_path, "x3", b"two"), retain_until=RETAIN)
    with writer.open("x.bin") as handle:
        assert handle.read() == b"one"


def test_finalize_refuses_existing_target(store: FilesystemArchiveStore, tmp_path: Path) -> None:
    writer = _staged(store, tmp_path)
    (store._layout.root / PREFIX.rstrip("/")).mkdir(parents=True)
    ((store._layout.root / PREFIX.rstrip("/")) / "other").write_bytes(b"x")

    with pytest.raises(RevisionExistsError):
        writer.finalize(retain_until=RETAIN)
    with pytest.raises(RevisionExistsError):
        store.begin_revision(PREFIX)


def test_resume_staged_revision(store: FilesystemArchiveStore, tmp_path: Path) -> None:
    _staged(store, tmp_path)

    with pytest.raises(ArchiveStoreError):
        store.begin_revision(PREFIX)
    resumed = store.begin_revision(PREFIX, resume=True)
    assert resumed.keys() == ["a/doc.pdf", "meta.json"]
    resumed.put_new("a/doc.pdf", _source(tmp_path, "d2", b"pdf"), retain_until=RETAIN)
    resumed.finalize(retain_until=RETAIN)

    assert sorted(store.list(PREFIX)) == [f"{PREFIX}a/doc.pdf", f"{PREFIX}meta.json"]


def test_resume_after_finalize(store: FilesystemArchiveStore, tmp_path: Path) -> None:
    _staged(store, tmp_path).finalize(retain_until=RETAIN)

    resumed = store.begin_revision(PREFIX, resume=True)
    again = resumed.put_new("meta.json", _source(tmp_path, "m2", b"{}"), retain_until=RETAIN)
    assert again.sha256 == hashlib.sha256(b"{}").hexdigest()
    with pytest.raises(ObjectExistsError):
        resumed.put_new("meta.json", _source(tmp_path, "m3", b"[]"), retain_until=RETAIN)
    with pytest.raises(RevisionExistsError):
        resumed.put_new("new.json", _source(tmp_path, "n", b"1"), retain_until=RETAIN)
    with resumed.open("a/doc.pdf") as handle:
        assert handle.read() == b"pdf"
    resumed.finalize(retain_until=RETAIN)
    resumed.abort()
    assert store.exists(PREFIX)
    assert resumed.keys() == ["a/doc.pdf", "meta.json"]


def test_finalized_files_and_directories_are_read_only(
    store: FilesystemArchiveStore, tmp_path: Path
) -> None:
    _staged(store, tmp_path).finalize(retain_until=RETAIN)
    final = store._layout.root / PREFIX.rstrip("/")

    _assert_read_only(final)


@pytest.mark.parametrize("key", ["../x", "a/../x", "/abs", "a\\b", "a//b", "./a", "", "a\x00b"])
def test_invalid_keys_are_rejected(store: FilesystemArchiveStore, tmp_path: Path, key: str) -> None:
    writer = store.begin_revision(PREFIX)
    with pytest.raises(ArchiveStoreError):
        writer.put_new(key, _source(tmp_path, "s", b"x"), retain_until=RETAIN)
    with pytest.raises(ArchiveStoreError):
        store.open(key)
    with pytest.raises(ArchiveStoreError):
        store.head(key)


@pytest.mark.parametrize(
    "prefix", ["a/b", "/a/", "../a/", "a/../b/", "a\\b/", "", f"{STAGING_DIR_NAME}/"]
)
def test_invalid_prefixes_are_rejected(store: FilesystemArchiveStore, prefix: str) -> None:
    with pytest.raises(ArchiveStoreError):
        store.begin_revision(prefix)
    with pytest.raises(ArchiveStoreError):
        store.exists(prefix)
    with pytest.raises(ArchiveStoreError):
        store.list(prefix)


def test_symlink_inside_root_is_rejected(store: FilesystemArchiveStore, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_bytes(b"s")
    store.begin_revision("seed/").abort()
    link = store._layout.root / "linked"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ArchiveStoreError):
        store.open("linked/secret")
    with pytest.raises(ArchiveStoreError):
        store.begin_revision("linked/rev/")
    with pytest.raises(ArchiveStoreError):
        store.exists("linked/rev/")
    assert not (outside / "rev").exists()


def test_staged_symlink_blocks_finalize(store: FilesystemArchiveStore, tmp_path: Path) -> None:
    writer = _staged(store, tmp_path)
    staging = next(p for p in store._layout.staging_root.iterdir() if p.is_dir())
    (staging / "evil").symlink_to(tmp_path / "doc.pdf")

    with pytest.raises(ArchiveStoreError):
        writer.finalize(retain_until=RETAIN)
    assert not store.exists(PREFIX)


def test_abort_removes_only_the_marked_staging_child(
    store: FilesystemArchiveStore, tmp_path: Path
) -> None:
    keep = _staged(store, tmp_path, "other/rev/")
    writer = _staged(store, tmp_path)
    sibling = store._layout.root / "precious"
    sibling.mkdir()
    (sibling / "file").write_bytes(b"keep")

    writer.abort()

    keep_name = _staging_dir(store, "other/rev/").name
    assert sorted(p.name for p in store._layout.staging_root.iterdir()) == sorted(
        [STAGING_MARKER_NAME, keep_name, f"{keep_name}{LOCK_SUFFIX}"]
    )
    assert keep.keys() == ["a/doc.pdf", "meta.json"]
    assert (sibling / "file").read_bytes() == b"keep"
    assert not store.exists(PREFIX)


def test_abort_and_cleanup_ignore_unmarked_staging_root(
    store: FilesystemArchiveStore, tmp_path: Path
) -> None:
    writer = _staged(store, tmp_path)
    staging_root = store._layout.staging_root
    (staging_root / STAGING_MARKER_NAME).unlink()
    victim = next(p for p in staging_root.iterdir() if p.is_dir())
    old = (NOW - timedelta(days=30)).timestamp()
    os.utime(victim, (old, old))

    writer.abort()
    removed = store.cleanup_abandoned_staging(older_than=timedelta(days=1), now=NOW)

    assert removed == 0
    assert victim.is_dir()
    assert (victim / "meta.json").read_bytes() == b"{}"


def test_cleanup_removes_only_old_marked_children(
    store: FilesystemArchiveStore, tmp_path: Path
) -> None:
    _staged(store, tmp_path, "old/rev/")
    _staged(store, tmp_path, "new/rev/")
    staging_root = store._layout.staging_root
    stray = staging_root / "not-a-staging-hash"
    stray.mkdir()
    old_dir = _staging_dir(store, "old/rev/")
    new_dir = _staging_dir(store, "new/rev/")
    _age(old_dir, LONG_AGO)
    _age(stray, LONG_AGO)
    _age(new_dir, RECENT)

    removed = store.cleanup_abandoned_staging(older_than=timedelta(days=1), now=NOW)

    assert removed == 1
    assert not old_dir.exists()
    assert not _lock_of(old_dir).exists()
    assert stray.is_dir()
    assert (staging_root / STAGING_MARKER_NAME).is_file()
    assert sorted(p.name for p in staging_root.iterdir()) == sorted(
        [STAGING_MARKER_NAME, stray.name, new_dir.name, _lock_of(new_dir).name]
    )


def test_list_exists_and_head(store: FilesystemArchiveStore, tmp_path: Path) -> None:
    _staged(store, tmp_path).finalize(retain_until=RETAIN)
    _staged(store, tmp_path, "tickets/42/rev-2/")

    assert list(store.list("tickets/42/")) == [
        f"{PREFIX}a/doc.pdf",
        f"{PREFIX}meta.json",
    ]
    assert list(store.list("missing/")) == []
    assert not any(STAGING_DIR_NAME in key for key in store.list("tickets/"))
    assert store.exists(PREFIX)
    assert not store.exists("tickets/42/rev-2/")
    info = store.head(f"{PREFIX}meta.json")
    assert (info.size, info.sha256, info.retain_until, info.legal_hold) == (2, None, None, False)
    with pytest.raises(FileNotFoundError):
        store.head(f"{PREFIX}absent")
    with pytest.raises(ArchiveStoreError):
        store.open(f"{STAGING_DIR_NAME}/{STAGING_MARKER_NAME}")


def test_retention_flag_reflects_operator_assertion(tmp_path: Path) -> None:
    assert FilesystemArchiveStore(tmp_path / "a").retention_enforced is False
    assert FilesystemArchiveStore(tmp_path / "b", worm_enforced=True).retention_enforced is True


def test_torn_staged_files_do_not_block_resume(
    store: FilesystemArchiveStore, tmp_path: Path
) -> None:
    _staged(store, tmp_path)  # writer dropped: simulated crash
    staging = _staging_dir(store)
    torn_temp = staging / "a" / f".doc.pdf.partial-{'0' * 32}"
    torn_temp.write_bytes(b"pd")
    (staging / "meta.json").write_bytes(b"{")

    resumed = store.begin_revision(PREFIX, resume=True)

    assert not torn_temp.exists()
    assert resumed.keys() == ["a/doc.pdf", "meta.json"]
    stored = resumed.put_new("meta.json", _source(tmp_path, "m", b"{}"), retain_until=RETAIN)
    assert stored.sha256 == hashlib.sha256(b"{}").hexdigest()
    late_temp = staging / f".meta.json.partial-{'1' * 32}"
    late_temp.write_bytes(b"x")
    assert resumed.keys() == ["a/doc.pdf", "meta.json"]
    resumed.finalize(retain_until=RETAIN)

    assert sorted(store.list(PREFIX)) == [f"{PREFIX}a/doc.pdf", f"{PREFIX}meta.json"]
    with store.open(f"{PREFIX}meta.json") as handle:
        assert handle.read() == b"{}"


def test_failed_copy_leaves_no_temporary_file(
    store: FilesystemArchiveStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = store.begin_revision(PREFIX)

    def failing_copy(fd: int, source: Path, *, fsync: bool) -> tuple[str, int]:
        """Write a little, then fail like an interrupted copy."""
        os.write(fd, b"partial")
        os.close(fd)
        raise OSError(errno.EIO, "simulated I/O error")

    monkeypatch.setattr(archive_fs, "_copy_to_fd", failing_copy)
    with pytest.raises(OSError, match="simulated"):
        writer.put_new("x.bin", _source(tmp_path, "x", b"data"), retain_until=RETAIN)

    assert os.listdir(_staging_dir(store)) == []
    with pytest.raises(ArchiveStoreError):
        writer.put_new(f".x.partial-{'2' * 32}", _source(tmp_path, "y", b"y"), retain_until=RETAIN)


def test_crash_between_publish_and_chmod_is_repaired(
    store: FilesystemArchiveStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_make_read_only = archive_fs._make_read_only
    calls: list[Path] = []

    def crash_once(layout: object, directory: Path) -> None:
        """Fail the first mode step, as if the process died after the rename."""
        calls.append(directory)
        if len(calls) == 1:
            raise OSError(errno.EIO, "simulated crash")
        real_make_read_only(layout, directory)  # type: ignore[arg-type]

    monkeypatch.setattr(archive_fs, "_make_read_only", crash_once)
    with pytest.raises(OSError, match="simulated crash"):
        _staged(store, tmp_path).finalize(retain_until=RETAIN)
    final = store._layout.root / PREFIX.rstrip("/")
    assert store.exists(PREFIX)
    assert (final / "meta.json").stat().st_mode & 0o777 != 0o440
    assert sorted(p.name for p in store._layout.staging_root.iterdir()) == [STAGING_MARKER_NAME]

    store.begin_revision(PREFIX, resume=True).finalize(retain_until=RETAIN)

    _assert_read_only(final)
    store.begin_revision(PREFIX, resume=True).finalize(retain_until=RETAIN)
    _assert_read_only(final)


def test_in_root_directory_symlink_is_rejected_by_store_reads(
    store: FilesystemArchiveStore, tmp_path: Path
) -> None:
    _staged(store, tmp_path, "rc/other/rev/").finalize(retain_until=RETAIN)
    rc = store._layout.root / "rc"
    (rc / "user").symlink_to(rc / "other", target_is_directory=True)

    with pytest.raises(ArchiveStoreError):
        store.open("rc/user/rev/meta.json")
    with pytest.raises(ArchiveStoreError):
        store.head("rc/user/rev/meta.json")
    with pytest.raises(ArchiveStoreError):
        store.list("rc/user/")
    with pytest.raises(ArchiveStoreError):
        store.list("rc/")
    with pytest.raises(ArchiveStoreError):
        store.exists("rc/user/rev/")
    with pytest.raises(ArchiveStoreError):
        store.begin_revision("rc/user/rev/", resume=True)
    with store.open("rc/other/rev/meta.json") as handle:
        assert handle.read() == b"{}"


def test_in_root_symlinks_are_rejected_by_writer_reads(
    store: FilesystemArchiveStore, tmp_path: Path
) -> None:
    writer = _staged(store, tmp_path)
    staging = _staging_dir(store)
    (staging / "user").symlink_to(staging / "a", target_is_directory=True)

    with pytest.raises(ArchiveStoreError):
        writer.open("user/doc.pdf")
    with pytest.raises(ArchiveStoreError):
        writer.keys()
    (staging / "user").unlink()
    (staging / "leaf.pdf").symlink_to(staging / "a" / "doc.pdf")
    with pytest.raises(ArchiveStoreError):
        writer.open("leaf.pdf")
    with pytest.raises(ArchiveStoreError):
        writer.put_new("leaf.pdf", _source(tmp_path, "l", b"pdf"), retain_until=RETAIN)


def test_empty_revision_is_not_published(store: FilesystemArchiveStore) -> None:
    writer = store.begin_revision(PREFIX)

    with pytest.raises(ArchiveStoreError, match="no objects"):
        writer.finalize(retain_until=RETAIN)
    assert not store.exists(PREFIX)
    writer.abort()


def test_lock_prevents_a_second_writer(store: FilesystemArchiveStore, tmp_path: Path) -> None:
    first = _staged(store, tmp_path)
    lock = _lock_of(_staging_dir(store))

    assert lock.is_file()
    with pytest.raises(ArchiveStoreError, match="another writer"):
        store.begin_revision(PREFIX, resume=True)
    with pytest.raises(ArchiveStoreError, match="another writer"):
        store.begin_revision(PREFIX)
    first.abort()
    assert not lock.exists()

    _staged(store, tmp_path)  # writer dropped: the lock file stays, its flock is gone
    assert lock.is_file()
    resumed = store.begin_revision(PREFIX, resume=True)
    later = datetime.now(UTC) + timedelta(days=1)
    assert store.cleanup_abandoned_staging(older_than=timedelta(0), now=later) == 0
    assert resumed.keys() == ["a/doc.pdf", "meta.json"]
    resumed.finalize(retain_until=RETAIN)
    assert not lock.exists()


def test_a_failing_flock_does_not_leak_the_lock_descriptor(
    store: FilesystemArchiveStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_locks(_fd: int, _operation: int) -> None:
        raise OSError(errno.ENOLCK, "no locks available")

    monkeypatch.setattr(archive_fs.fcntl, "flock", no_locks)
    before = len(os.listdir("/dev/fd"))
    for _ in range(3):
        with pytest.raises(OSError, match="no locks"):
            store.begin_revision(PREFIX)

    assert len(os.listdir("/dev/fd")) == before


def test_cleanup_uses_newest_nested_mtime_and_respects_locks(
    store: FilesystemArchiveStore, tmp_path: Path
) -> None:
    _staged(store, tmp_path, "nested/rev/")
    nested = _staging_dir(store, "nested/rev/")
    _age(nested, LONG_AGO)
    os.utime(nested / "a" / "doc.pdf", (RECENT.timestamp(), RECENT.timestamp()))
    held = _staged(store, tmp_path, "held/rev/")
    _age(_staging_dir(store, "held/rev/"), LONG_AGO)
    _staged(store, tmp_path, "young-lock/rev/")
    young = _staging_dir(store, "young-lock/rev/")
    _age(young, LONG_AGO)
    os.utime(_lock_of(young), (RECENT.timestamp(), RECENT.timestamp()))

    assert store.cleanup_abandoned_staging(older_than=timedelta(days=1), now=NOW) == 0
    assert nested.is_dir()
    assert _staging_dir(store, "held/rev/").is_dir()
    assert young.is_dir()

    _age(nested, LONG_AGO)
    assert store.cleanup_abandoned_staging(older_than=timedelta(days=1), now=NOW) == 1
    assert not nested.exists()
    assert not _lock_of(nested).exists()
    assert held.keys() == ["a/doc.pdf", "meta.json"]


def test_symlinked_root_ancestor_is_named(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "link").symlink_to(real, target_is_directory=True)

    with pytest.raises(ArchiveStoreError, match="link is a symlink.*symlink-free absolute path"):
        FilesystemArchiveStore(tmp_path / "link" / "archive")


def test_non_directory_root_component_is_a_store_error(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_bytes(b"")
    store = FilesystemArchiveStore(blocker / "archive", fsync=False)

    with pytest.raises(ArchiveStoreError):
        store.begin_revision(PREFIX)
    with pytest.raises(ArchiveStoreError):
        store.exists(PREFIX)


@pytest.fixture(params=["filesystem", "fake"])
def any_store(request: pytest.FixtureRequest, store: FilesystemArchiveStore) -> ArchiveStore:
    """Provide the filesystem store and the in-memory fake for parity checks."""
    return store if request.param == "filesystem" else FakeArchiveStore()


def test_stores_agree_on_empty_finalize_exists_and_resume(
    any_store: ArchiveStore, tmp_path: Path
) -> None:
    empty = any_store.begin_revision("empty/rev/")
    with pytest.raises(ArchiveStoreError, match="no objects"):
        empty.finalize(retain_until=RETAIN)
    empty.abort()
    assert not any_store.exists("empty/rev/")

    any_store.begin_revision(PREFIX).put_new(
        "meta.json", _source(tmp_path, "m1", b"{}"), retain_until=RETAIN
    )
    with pytest.raises(ArchiveStoreError):
        any_store.begin_revision(PREFIX)
    resumed = any_store.begin_revision(PREFIX, resume=True)
    assert resumed.keys() == ["meta.json"]
    resumed.put_new("meta.json", _source(tmp_path, "m2", b"[]"), retain_until=RETAIN)
    with pytest.raises(ObjectExistsError):
        resumed.put_new("meta.json", _source(tmp_path, "m3", b"{}"), retain_until=RETAIN)
    resumed.finalize(retain_until=RETAIN)

    assert any_store.exists(PREFIX)
    assert any_store.exists("tickets/42/")
    assert not any_store.exists("tickets/4/")
    with any_store.open(f"{PREFIX}meta.json") as handle:
        assert handle.read() == b"[]"
    final = any_store.begin_revision(PREFIX, resume=True)
    final.put_new("meta.json", _source(tmp_path, "m4", b"[]"), retain_until=RETAIN)
    with pytest.raises(ObjectExistsError):
        final.put_new("meta.json", _source(tmp_path, "m5", b"{}"), retain_until=RETAIN)
    with pytest.raises(RevisionExistsError):
        final.put_new("new.json", _source(tmp_path, "n", b"1"), retain_until=RETAIN)
    final.finalize(retain_until=RETAIN)
