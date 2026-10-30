"""Store write-once archive revisions on a filesystem below a confined root.

Revisions are staged in ``<root>/.chronikwerk-staging/<hash>/`` and published with one
directory rename, so a final prefix either does not exist or is complete. Published files
are made read-only (0o440, directories 0o550) through descriptors opened without following
symlinks; ``finalize`` re-applies the modes idempotently, so a crash between rename and
chmod is repaired by resuming. ``retain_until`` cannot be recorded on a plain filesystem;
with ``worm_enforced`` the operator asserts that the share enforces retention itself.

Each object is written to ``.<leaf>.partial-<hex>`` next to its key, synced and then
hard-linked to the key, so a crash leaves at most a ``.partial-*`` file. Those files are
invisible to ``keys`` and are removed when the revision is resumed or published. Every
read walks the tree through descriptors with ``O_NOFOLLOW``; a symlink anywhere below the
root is an ``ArchiveStoreError``.

POSIX ``rename`` silently replaces an *empty* directory at the target. ``finalize`` checks
that the final prefix is absent, and every staged writer holds a per-prefix lock
(``<staging root>/<hash>.lock``, created with ``O_EXCL`` and held with ``flock``) from
``begin_revision`` until ``finalize`` or ``abort``. Every writer of a prefix needs that
lock, so no second writer in this process or on this host can create the target between
the check and the rename. A crashed writer's lock file stays behind but its ``flock`` is
gone, so resume takes it over. The lock does not cover other hosts sharing the archive or
foreign processes creating directories there, and revision prefixes must not be nested.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import re
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO

from chronikwerk.storage.archive_store import (
    ArchiveStoreError,
    ObjectExistsError,
    RevisionExistsError,
    StoredObject,
    validate_key,
    validate_prefix,
)
from chronikwerk.storage.filesystem import (
    _fsync_fd_best_effort,
    ensure_dir,
    move_file_within_root,
    open_directory_within_root,
    open_file_within_root,
    open_parent_within_root,
    open_regular_file_at,
    path_entry_exists,
    remove_tree_within_root,
    unlink_file_within_root,
    walk_tree_within_root,
)

STAGING_DIR_NAME = ".chronikwerk-staging"
STAGING_MARKER_NAME = ".chronikwerk-staging"
LOCK_SUFFIX = ".lock"
FILE_MODE = 0o440
DIRECTORY_MODE = 0o550
_STAGING_ENTRY_RE = re.compile(r"[0-9a-f]{32}")
_LOCK_ENTRY_RE = re.compile(r"[0-9a-f]{32}\.lock")
_PARTIAL_RE = re.compile(r"\..+\.partial-[0-9a-f]{32}")
_CHUNK_SIZE = 1024 * 1024
_SAFE_OPEN_FLAGS = getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _SAFE_OPEN_FLAGS
_LOCK_OPEN_FLAGS = os.O_RDONLY | _SAFE_OPEN_FLAGS | getattr(os, "O_NONBLOCK", 0)


def _symlinked_component(root: Path) -> Path | None:
    """Return the first existing component of ``root`` that is a symlink."""
    current = Path(root.anchor)
    for part in root.parts[1:]:
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError, NotADirectoryError:
            return None
        if stat.S_ISLNK(mode):
            return current
    return None


def _root_error(root: Path) -> ArchiveStoreError | None:
    component = _symlinked_component(root)
    if component is None:
        return None
    return ArchiveStoreError(
        f"archive root component {component} is a symlink; "
        "the archive root must be a symlink-free absolute path"
    )


@contextmanager
def _confined(root: Path) -> Iterator[None]:
    """Report path-safety ``ValueError``s from the filesystem layer as store errors."""
    try:
        yield
    except ValueError as exc:
        raise (_root_error(root) or ArchiveStoreError(str(exc))) from exc


def _is_partial(name: str) -> bool:
    return _PARTIAL_RE.fullmatch(name.rsplit("/", 1)[-1]) is not None


def _visible(found: list[str]) -> list[str]:
    return [key for key in found if not _is_partial(key)]


def _hash_handle(handle: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := handle.read(_CHUNK_SIZE):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _source_digest(source: Path) -> str:
    with source.open("rb") as handle:
        return _hash_handle(handle)[0]


def _digest_at(parent_fd: int, leaf: str) -> tuple[str, int] | None:
    """Hash an existing regular file, or return None when nothing is stored there."""
    try:
        fd = open_regular_file_at(parent_fd, leaf)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as handle:
        return _hash_handle(handle)


def _copy_to_fd(fd: int, source: Path, *, fsync: bool) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with os.fdopen(fd, "wb") as out, source.open("rb") as handle:
        while chunk := handle.read(_CHUNK_SIZE):
            digest.update(chunk)
            out.write(chunk)
            size += len(chunk)
        out.flush()
        if fsync:
            os.fsync(out.fileno())
    return digest.hexdigest(), size


def _unlink_quietly(parent_fd: int, leaf: str) -> None:
    with suppress(FileNotFoundError):
        os.unlink(leaf, dir_fd=parent_fd)


def _is_staging_child(staging_root: Path, path: Path) -> bool:
    """Decide whether ``path`` is a removable staging directory (pure guard)."""
    if path.parent != staging_root or _STAGING_ENTRY_RE.fullmatch(path.name) is None:
        return False
    try:
        marker = os.lstat(staging_root / STAGING_MARKER_NAME)
        entry = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(marker.st_mode) and stat.S_ISDIR(entry.st_mode)


def _is_lock_file(staging_root: Path, path: Path) -> bool:
    """Decide whether ``path`` is a removable per-prefix lock file (pure guard)."""
    if path.parent != staging_root or _LOCK_ENTRY_RE.fullmatch(path.name) is None:
        return False
    try:
        marker = os.lstat(staging_root / STAGING_MARKER_NAME)
        entry = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(marker.st_mode) and stat.S_ISREG(entry.st_mode)


def _remove_staging_dir(staging_root: Path, path: Path, *, fsync: bool) -> bool:
    if not _is_staging_child(staging_root, path):
        return False
    remove_tree_within_root(path, storage_root=staging_root, missing_ok=True, fsync=fsync)
    return True


@dataclass(frozen=True, slots=True)
class _Layout:
    root: Path
    staging_root: Path
    fsync: bool


def _open_lock_file(parent_fd: int, leaf: str) -> int:
    try:
        return os.open(leaf, _CREATE_FLAGS, 0o600, dir_fd=parent_fd)
    except FileExistsError:
        pass
    try:
        return os.open(leaf, _LOCK_OPEN_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        raise ArchiveStoreError("revision lock changed concurrently; retry") from None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ArchiveStoreError("revision lock file is a symlink") from exc
        raise


def _holds_lock(fd: int, parent_fd: int, leaf: str) -> bool:
    """Take ``flock`` on ``fd`` and confirm the file is still the one at ``leaf``."""
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    try:
        current = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    opened = os.fstat(fd)
    return (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino)


class _PrefixLock:
    """Per-prefix writer lock: an ``O_EXCL`` lock file in the staging root, held by flock."""

    def __init__(self, layout: _Layout, name: str, label: str) -> None:
        self._fd: int | None = None
        self._layout = layout
        self._path = layout.staging_root / f"{name}{LOCK_SUFFIX}"
        root = layout.root
        with open_parent_within_root(self._path, storage_root=root) as (parent_fd, leaf):
            fd = _open_lock_file(parent_fd, leaf)
            if not _holds_lock(fd, parent_fd, leaf):
                os.close(fd)
                raise ArchiveStoreError(f"revision {label} is being written by another writer")
        self._fd = fd

    def release(self) -> None:
        """Remove the lock file while still holding it, then drop the lock."""
        if self._fd is None:
            return
        try:
            if _is_lock_file(self._layout.staging_root, self._path):
                unlink_file_within_root(
                    self._path,
                    storage_root=self._layout.staging_root,
                    missing_ok=True,
                    fsync=False,
                )
        finally:
            os.close(self._fd)
            self._fd = None

    def __del__(self) -> None:
        """Drop the flock (not the file) of a writer discarded without finishing."""
        fd = getattr(self, "_fd", None)
        if fd is not None:
            with suppress(OSError):
                os.close(fd)


def _sweep_staging(layout: _Layout, directory: Path, *, fsync: bool) -> None:
    """Remove leftover ``.partial-*`` files of a staged revision and optionally sync it."""

    def on_file(parent_fd: int, leaf: str) -> None:
        if _is_partial(leaf):
            _unlink_quietly(parent_fd, leaf)
        elif fsync:
            fd = open_regular_file_at(parent_fd, leaf)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def on_dir(fd: int) -> None:
        if fsync:
            _fsync_fd_best_effort(fd)

    walk_tree_within_root(directory, storage_root=layout.root, on_file=on_file, on_dir=on_dir)


def _make_read_only(layout: _Layout, directory: Path) -> None:
    """Apply read-only modes through descriptors, never following a symlink."""

    def on_file(parent_fd: int, leaf: str) -> None:
        fd = open_regular_file_at(parent_fd, leaf)
        try:
            os.fchmod(fd, FILE_MODE)
        finally:
            os.close(fd)

    def on_dir(fd: int) -> None:
        os.fchmod(fd, DIRECTORY_MODE)

    walk_tree_within_root(directory, storage_root=layout.root, on_file=on_file, on_dir=on_dir)


class _FilesystemRevisionWriter:
    """Create-only writer for one revision, staged or (on resume) already final."""

    def __init__(
        self,
        layout: _Layout,
        prefix: str,
        directory: Path,
        *,
        final: bool,
        lock: _PrefixLock | None = None,
    ) -> None:
        self.prefix = prefix
        self._layout = layout
        self._dir = directory
        self._final = final
        self._lock = lock
        self._written: set[str] = set()

    def put_new(self, relative_key: str, source: Path, *, retain_until: datetime) -> StoredObject:
        """Copy ``source`` into the revision without ever overwriting a published object."""
        _ = retain_until  # not recordable on a plain filesystem
        key = validate_key(relative_key)
        with _confined(self._layout.root):
            digest, size = (
                self._verify_final(key, source) if self._final else self._stage(key, source)
            )
        self._written.add(key)
        return StoredObject(f"{self.prefix}{key}", size, digest, None, False)

    def _stage(self, key: str, source: Path) -> tuple[str, int]:
        if _is_partial(key):
            raise ArchiveStoreError("key collides with the temporary-file naming scheme")
        target = self._dir / key
        root = self._layout.root
        with open_parent_within_root(target, storage_root=root, create=True) as (parent_fd, leaf):
            existing = _digest_at(parent_fd, leaf)
            if existing is not None:
                if existing[0] == _source_digest(source):
                    return existing
                if key in self._written:
                    raise ObjectExistsError(
                        f"object {self.prefix}{key} exists with different content"
                    )
            # A differing object this writer did not store is a leftover; staging is private.
            return self._copy_in(parent_fd, leaf, source, replace=existing is not None)

    def _copy_in(
        self, parent_fd: int, leaf: str, source: Path, *, replace: bool
    ) -> tuple[str, int]:
        temp = f".{leaf}.partial-{uuid.uuid4().hex}"
        fd = os.open(temp, _CREATE_FLAGS, 0o640, dir_fd=parent_fd)
        try:
            stored = _copy_to_fd(fd, source, fsync=self._layout.fsync)
            if replace:
                os.unlink(leaf, dir_fd=parent_fd)
            os.link(temp, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        except FileExistsError as exc:
            raise ObjectExistsError(f"object {self.prefix}{leaf} appeared concurrently") from exc
        finally:
            _unlink_quietly(parent_fd, temp)
        if self._layout.fsync:
            _fsync_fd_best_effort(parent_fd)
        return stored

    def _verify_final(self, key: str, source: Path) -> tuple[str, int]:
        root = self._layout.root
        try:
            with open_parent_within_root(self._dir / key, storage_root=root) as (parent_fd, leaf):
                existing = _digest_at(parent_fd, leaf)
        except FileNotFoundError:
            existing = None
        if existing is None:
            raise RevisionExistsError(f"revision {self.prefix} is already final")
        if existing[0] != _source_digest(source):
            raise ObjectExistsError(f"object {self.prefix}{key} exists with different content")
        return existing

    def open(self, relative_key: str) -> BinaryIO:
        """Read back an object of this revision."""
        path = self._dir / validate_key(relative_key)
        with _confined(self._layout.root):
            return os.fdopen(open_file_within_root(path, storage_root=self._layout.root), "rb")

    def keys(self) -> list[str]:
        """Return the sorted relative keys of this revision."""
        with _confined(self._layout.root):
            try:
                found = walk_tree_within_root(self._dir, storage_root=self._layout.root)
            except FileNotFoundError:
                return []
        return _visible(found)

    def finalize(self, *, retain_until: datetime) -> None:
        """Publish the staged revision atomically, then (re-)apply read-only modes."""
        _ = retain_until  # not recordable on a plain filesystem
        with _confined(self._layout.root):
            if not self._final:
                self._publish()
            _make_read_only(self._layout, self._dir)

    def _publish(self) -> None:
        if not self.keys():
            raise ArchiveStoreError(f"revision {self.prefix} has no objects; refusing to publish")
        root = self._layout.root
        final = root / self.prefix.rstrip("/")
        _sweep_staging(self._layout, self._dir, fsync=self._layout.fsync)
        if path_entry_exists(final, storage_root=root):
            raise RevisionExistsError(f"revision {self.prefix} already exists")
        try:
            move_file_within_root(self._dir, final, storage_root=root, fsync=self._layout.fsync)
        except FileNotFoundError as exc:
            raise ArchiveStoreError(
                f"staged revision {self.prefix} disappeared while it was being published"
            ) from exc
        except OSError as exc:
            if exc.errno in {errno.ENOTEMPTY, errno.EEXIST}:
                raise RevisionExistsError(
                    f"revision {self.prefix} was published concurrently"
                ) from exc
            raise
        self._dir = final
        self._final = True
        self._release_lock()

    def _release_lock(self) -> None:
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    def abort(self) -> None:
        """Remove the staged revision; a published revision is never touched."""
        if self._final:
            return
        with _confined(self._layout.root):
            try:
                _remove_staging_dir(self._layout.staging_root, self._dir, fsync=self._layout.fsync)
            finally:
                self._release_lock()


def _newest_mtime(layout: _Layout, path: Path, lock_path: Path) -> float | None:
    """Return the newest lstat mtime in a staging tree and its lock, None if it vanished."""
    times: list[float] = []

    def on_entry(parent_fd: int, leaf: str) -> None:
        with suppress(FileNotFoundError):
            times.append(os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False).st_mtime)

    def on_dir(fd: int) -> None:
        times.append(os.fstat(fd).st_mtime)

    try:
        walk_tree_within_root(
            path, storage_root=layout.root, on_file=on_entry, on_dir=on_dir, on_symlink=on_entry
        )
    except FileNotFoundError, ValueError:
        return None
    with suppress(FileNotFoundError):
        times.append(os.lstat(lock_path).st_mtime)
    return max(times)


class FilesystemArchiveStore:
    """Filesystem implementation of the write-once ``ArchiveStore`` protocol."""

    def __init__(self, root: Path, *, worm_enforced: bool = False, fsync: bool = True) -> None:
        absolute = Path(os.path.abspath(os.fspath(root)))
        if (error := _root_error(absolute)) is not None:
            raise error
        self._layout = _Layout(absolute, absolute / STAGING_DIR_NAME, fsync)
        self.retention_enforced = worm_enforced

    def begin_revision(self, prefix: str, *, resume: bool = False) -> _FilesystemRevisionWriter:
        """Start a staged revision, or resume a staged or already published one."""
        final = self._final_path(validate_prefix(prefix).rstrip("/"))
        with _confined(self._layout.root):
            if self._published(final):
                if not resume:
                    raise RevisionExistsError(f"revision {prefix} already exists")
                return _FilesystemRevisionWriter(self._layout, prefix, final, final=True)
            return self._begin_staged(prefix, resume=resume)

    def _begin_staged(self, prefix: str, *, resume: bool) -> _FilesystemRevisionWriter:
        self._ensure_staging_root()
        name = hashlib.sha256(prefix.encode()).hexdigest()[:32]
        lock = _PrefixLock(self._layout, name, prefix)
        try:
            staging = self._create_staging_dir(name, prefix, resume=resume)
        except BaseException:
            lock.release()
            raise
        return _FilesystemRevisionWriter(self._layout, prefix, staging, final=False, lock=lock)

    def _create_staging_dir(self, name: str, prefix: str, *, resume: bool) -> Path:
        staging = self._layout.staging_root / name
        root = self._layout.root
        with open_directory_within_root(self._layout.staging_root, storage_root=root) as fd:
            try:
                os.mkdir(name, 0o700, dir_fd=fd)
                return staging
            except FileExistsError:
                if not resume:
                    raise ArchiveStoreError(f"revision {prefix} is already being staged") from None
        _sweep_staging(self._layout, staging, fsync=False)
        return staging

    def _ensure_staging_root(self) -> None:
        staging_root = self._layout.staging_root
        ensure_dir(self._layout.root)
        try:
            os.mkdir(staging_root, 0o700)
        except FileExistsError:
            if staging_root.is_symlink() or not staging_root.is_dir():
                raise ArchiveStoreError("staging root is not a plain directory") from None
        try:
            fd = os.open(staging_root / STAGING_MARKER_NAME, _CREATE_FLAGS, 0o600)
        except FileExistsError:
            return
        os.close(fd)

    def _final_path(self, relative: str) -> Path:
        if relative.split("/", 1)[0] == STAGING_DIR_NAME:
            raise ArchiveStoreError("key or prefix collides with the staging area")
        return self._layout.root / relative

    def _published(self, final: Path) -> bool:
        """Report whether ``final`` is a revision directory; other entries are refused."""
        try:
            with open_parent_within_root(final, storage_root=self._layout.root) as (fd, leaf):
                mode = os.stat(leaf, dir_fd=fd, follow_symlinks=False).st_mode
        except FileNotFoundError:
            return False
        if not stat.S_ISDIR(mode):
            raise ArchiveStoreError(f"prefix {final.name}/ is occupied by a non-directory entry")
        return True

    def open(self, key: str) -> BinaryIO:
        """Open a published object for reading."""
        path = self._final_path(validate_key(key))
        with _confined(self._layout.root):
            return os.fdopen(open_file_within_root(path, storage_root=self._layout.root), "rb")

    def head(self, key: str) -> StoredObject:
        """Return size and flags of a published object without hashing it."""
        path = self._final_path(validate_key(key))
        with _confined(self._layout.root):
            fd = open_file_within_root(path, storage_root=self._layout.root)
        try:
            size = os.fstat(fd).st_size
        finally:
            os.close(fd)
        return StoredObject(key, size, None, None, False)

    def list(self, prefix: str) -> Iterator[str]:
        """Yield published keys below ``prefix`` (never staging content)."""
        directory = self._final_path(validate_prefix(prefix).rstrip("/"))
        with _confined(self._layout.root):
            try:
                found = walk_tree_within_root(directory, storage_root=self._layout.root)
            except FileNotFoundError:
                found = []
        return iter([prefix + relative for relative in _visible(found)])

    def exists(self, prefix: str) -> bool:
        """Report whether a published revision directory exists at ``prefix``."""
        path = self._final_path(validate_prefix(prefix).rstrip("/"))
        with _confined(self._layout.root):
            return self._published(path)

    def cleanup_abandoned_staging(self, *, older_than: timedelta, now: datetime) -> int:
        """Remove unlocked, marked staging directories with nothing modified for ``older_than``."""
        cutoff = (now - older_than).timestamp()
        try:
            candidates = [Path(entry.path) for entry in os.scandir(self._layout.staging_root)]
        except FileNotFoundError:
            return 0
        with _confined(self._layout.root):
            return sum(self._remove_if_abandoned(path, cutoff) for path in sorted(candidates))

    def _remove_if_abandoned(self, path: Path, cutoff: float) -> bool:
        staging_root = self._layout.staging_root
        if not _is_staging_child(staging_root, path):
            return False
        newest = _newest_mtime(self._layout, path, staging_root / f"{path.name}{LOCK_SUFFIX}")
        if newest is None or newest >= cutoff:
            return False
        try:
            lock = _PrefixLock(self._layout, path.name, path.name)
        except ArchiveStoreError:
            return False
        try:
            return _remove_staging_dir(staging_root, path, fsync=self._layout.fsync)
        finally:
            lock.release()
