"""Persist archived PDFs safely through the archive filesystem boundary."""

from __future__ import annotations

import errno
import os
import shutil
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from chronikwerk.storage.policy import ensure_within_root

_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_HAS_SECURE_FILESYSTEM_PRIMITIVES = bool(
    getattr(os, "O_DIRECTORY", 0)
    and getattr(os, "O_NOFOLLOW", 0)
    and all(call in os.supports_dir_fd for call in (os.open, os.mkdir, os.rename, os.unlink))
)
_RMTREE_AVOIDS_SYMLINK_ATTACKS = shutil.rmtree.avoids_symlink_attacks


def _require_secure_filesystem_primitives() -> None:
    if not _HAS_SECURE_FILESYSTEM_PRIMITIVES:
        raise RuntimeError(
            "secure archive storage requires POSIX descriptor-relative filesystem operations"
        )


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _unsafe_directory_component(exc: OSError) -> bool:
    return exc.errno in {errno.ELOOP, errno.ENOTDIR}


def _open_child_directory(parent_fd: int, name: str, *, create: bool) -> int:
    if create:
        try:
            os.mkdir(name, mode=0o750, dir_fd=parent_fd)
        except FileExistsError:
            pass

    try:
        return os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        if _unsafe_directory_component(exc):
            raise ValueError("target path traverses a symlink or non-directory component") from exc
        raise


@contextmanager
def _open_directory(path: Path, *, create: bool) -> Iterator[int]:
    """Open an absolute directory without following any path-component symlink."""
    _require_secure_filesystem_primitives()
    absolute = _absolute_path(path)
    fd = os.open(os.sep, _DIRECTORY_OPEN_FLAGS)
    try:
        for part in absolute.parts[1:]:
            child_fd = _open_child_directory(fd, part, create=create)
            os.close(fd)
            fd = child_fd
        yield fd
    finally:
        os.close(fd)


def ensure_dir(path: Path) -> None:
    """Create a directory tree without following symlinks in any component."""
    with _open_directory(path, create=True):
        pass


def _fsync_dir_best_effort(dir_path: Path) -> None:
    """
    Best-effort directory fsync after atomic replace.

    This improves durability across crashes on POSIX filesystems. Some platforms /
    filesystems may not support fsync on directories; failures are ignored.
    """
    try:
        fd = os.open(str(dir_path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        return
    finally:
        os.close(fd)


def _validate_and_prepare(target_path: Path, storage_root: Path) -> tuple[Path, Path]:
    """Validate path safety and ensure parent directory exists. Returns (target, parent)."""
    target = Path(target_path)
    parent = target.parent
    # Bug #13/#20: validate path and symlinks before any directory creation.
    ensure_within_root(storage_root, target)
    _reject_symlinks_under_root(storage_root, parent)
    ensure_dir(parent)
    return target, parent


def _relative_to_storage_root(storage_root: Path, path: Path) -> Path:
    root = _absolute_path(storage_root)
    target = _absolute_path(path)
    try:
        return target.relative_to(root)
    except ValueError as exc:
        raise ValueError("target path escapes root") from exc


@contextmanager
def _open_parent_under_root(
    storage_root: Path,
    path: Path,
    *,
    create: bool,
) -> Iterator[tuple[int, str]]:
    """Open ``path.parent`` from a stable root descriptor and return its leaf name."""
    relative = _relative_to_storage_root(storage_root, path)
    if not relative.parts:
        raise ValueError("target path must name a file below storage root")

    with _open_directory(storage_root, create=create) as root_fd:
        parent_fd = os.dup(root_fd)
        try:
            for part in relative.parts[:-1]:
                child_fd = _open_child_directory(parent_fd, part, create=create)
                os.close(parent_fd)
                parent_fd = child_fd
            yield parent_fd, relative.parts[-1]
        finally:
            os.close(parent_fd)


def path_entry_exists(path: Path, *, storage_root: Path) -> bool:
    """Check for a path entry without following its leaf or parent symlinks."""
    try:
        with _open_parent_under_root(storage_root, path, create=False) as (parent_fd, leaf):
            os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return True


def unlink_file_within_root(
    path: Path,
    *,
    storage_root: Path,
    missing_ok: bool = False,
    fsync: bool = True,
) -> None:
    """Unlink one entry beneath storage_root without following symlinks."""
    _remove_within_root(
        path,
        storage_root=storage_root,
        missing_ok=missing_ok,
        fsync=fsync,
        remover=lambda parent_fd, leaf: os.unlink(leaf, dir_fd=parent_fd),
    )


def _remove_within_root(
    path: Path,
    *,
    storage_root: Path,
    missing_ok: bool,
    fsync: bool,
    remover: Callable[[int, str], None],
) -> None:
    try:
        with _open_parent_under_root(storage_root, path, create=False) as (parent_fd, leaf):
            remover(parent_fd, leaf)
            if fsync:
                _fsync_fd_best_effort(parent_fd)
    except FileNotFoundError:
        if not missing_ok:
            raise


def remove_tree_within_root(
    path: Path,
    *,
    storage_root: Path,
    missing_ok: bool = False,
    fsync: bool = True,
) -> None:
    """Remove a directory tree beneath storage_root without following path swaps."""
    if not _RMTREE_AVOIDS_SYMLINK_ATTACKS:
        raise RuntimeError("secure archive cleanup requires symlink-safe shutil.rmtree")

    _remove_within_root(
        path,
        storage_root=storage_root,
        missing_ok=missing_ok,
        fsync=fsync,
        remover=lambda parent_fd, leaf: shutil.rmtree(leaf, dir_fd=parent_fd),
    )


def _fsync_fd_best_effort(fd: int) -> None:
    try:
        os.fsync(fd)
    except OSError:
        pass


def _unlink_temp_best_effort(parent_fd: int, temp_leaf: str) -> None:
    try:
        os.unlink(temp_leaf, dir_fd=parent_fd)
    except OSError:
        pass


def _write_temp_and_replace(
    parent_fd: int,
    target_leaf: str,
    data: bytes,
    *,
    fsync: bool,
) -> None:
    temp_leaf = f".tmp-{uuid.uuid4().hex}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(temp_leaf, flags, 0o640, dir_fd=parent_fd)
        with os.fdopen(fd, "wb") as file_obj:
            file_obj.write(data)
            file_obj.flush()
            os.fchmod(file_obj.fileno(), 0o640)
            if fsync:
                os.fsync(file_obj.fileno())

        os.replace(
            temp_leaf,
            target_leaf,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
        )
    except Exception:
        _unlink_temp_best_effort(parent_fd, temp_leaf)
        raise


def write_bytes(target_path: Path, data: bytes, *, storage_root: Path, fsync: bool = True) -> None:
    """Atomically write within storage_root without following any path symlink."""
    target, parent = _validate_and_prepare(target_path, storage_root)

    with _open_parent_under_root(storage_root, target, create=True) as (parent_fd, leaf):
        _write_temp_and_replace(
            parent_fd,
            leaf,
            data,
            fsync=fsync,
        )
        if fsync:
            _fsync_fd_best_effort(parent_fd)

    # Retain the existing best-effort behavior for filesystems that only accept a
    # path-opened directory descriptor. The security property does not depend on it.
    if fsync:
        _fsync_dir_best_effort(parent)


def _reject_symlinks_under_root(root: Path, target_dir: Path) -> None:
    """
    Reject target_dir if it traverses a symlink under root.

    This provides an early, readable validation error. Filesystem mutations do not
    rely on this check: they use descriptor-relative operations to close the race
    between validation and mutation.
    """
    root_resolved = Path(root).resolve(strict=False)
    dir_resolved = Path(target_dir).resolve(strict=False)
    ensure_within_root(root_resolved, dir_resolved)

    try:
        relative = dir_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("target path escapes root") from exc

    current = root_resolved
    for part in relative.parts:
        current = current / part
        try:
            if current.is_symlink():
                raise ValueError("target path traverses a symlink under storage root")
        except OSError as exc:
            # If the path is unreadable, treat it as unsafe.
            raise ValueError("target path validation failed (unreadable component)") from exc


def move_file_within_root(
    src: Path,
    dst: Path,
    *,
    storage_root: Path,
    fsync: bool = True,
) -> None:
    """
    Move a file from src to dst after validating both are within storage_root and dst
    doesn't traverse symlinks.
    """
    src = Path(src)
    dst = Path(dst)

    ensure_within_root(storage_root, src)
    ensure_within_root(storage_root, dst)
    _reject_symlinks_under_root(storage_root, dst.parent)

    ensure_dir(dst.parent)
    with _open_parent_under_root(storage_root, src, create=False) as (
        src_parent_fd,
        src_leaf,
    ):
        with _open_parent_under_root(storage_root, dst, create=True) as (
            dst_parent_fd,
            dst_leaf,
        ):
            os.replace(
                src_leaf,
                dst_leaf,
                src_dir_fd=src_parent_fd,
                dst_dir_fd=dst_parent_fd,
            )
            if fsync:
                try:
                    os.fsync(dst_parent_fd)
                except OSError:
                    pass

    if fsync:
        _fsync_dir_best_effort(dst.parent)
