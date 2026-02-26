from __future__ import annotations

import os
import tempfile
from pathlib import Path

from zammad_pdf_archiver.domain.path_policy import ensure_within_root


def ensure_dir(path: Path) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


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
        pass
    finally:
        os.close(fd)


def write_bytes(
    target_path: Path, data: bytes, *, storage_root: Path, fsync: bool = True
) -> None:
    target = Path(target_path)
    parent = target.parent
    # Bug #13/#20: validate path and symlinks before any directory creation.
    ensure_within_root(storage_root, target)
    _reject_symlinks_under_root(storage_root, parent)
    ensure_dir(parent)

    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(str(target), flags, 0o640)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        # Bug #40: always set permissions (e.g. when overwriting existing file).
        os.fchmod(f.fileno(), 0o640)
        if fsync:
            os.fsync(f.fileno())

    if fsync:
        _fsync_dir_best_effort(parent)


def _reject_symlinks_under_root(root: Path, target_dir: Path) -> None:
    """
    Reject target_dir if it traverses a symlink under root (best-effort).
    Note: TOCTOU race is possible (symlink created between check and write).
    """
    root_resolved = Path(root).resolve(strict=False)
    dir_resolved = Path(target_dir).resolve(strict=False)
    ensure_within_root(root_resolved, dir_resolved)

    try:
        relative = dir_resolved.relative_to(root_resolved)
    except Exception as exc:  # pragma: no cover
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


def write_atomic_bytes(
    target_path: Path, data: bytes, *, storage_root: Path, fsync: bool = True
) -> None:
    target = Path(target_path)
    parent = target.parent
    # Bug #13/#20: validate path and symlinks before any directory creation.
    ensure_within_root(storage_root, target)
    _reject_symlinks_under_root(storage_root, parent)
    ensure_dir(parent)

    tmp_path: Path | None = None
    fd: int | None = None

    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(parent), prefix=".tmp-")
        tmp_path = Path(tmp_name)
        _write_tmp_file(fd, data, fsync=fsync)
        fd = None

        _replace_tmp_with_target(tmp_path, target)

        if fsync:
            _fsync_dir_best_effort(parent)
    except Exception:
        _safe_close(fd)
        _safe_unlink(tmp_path)
        raise


def _write_tmp_file(fd: int, data: bytes, *, fsync: bool) -> None:
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        # Bug #21: set mode on fd before replace so target gets correct permissions.
        os.fchmod(f.fileno(), 0o640)
        if fsync:
            os.fsync(f.fileno())


def _replace_tmp_with_target(tmp_path: Path, target: Path) -> None:
    try:
        os.replace(tmp_path, target)
    except Exception:
        _safe_unlink(tmp_path)
        raise


def _safe_close(fd: int | None) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        pass


def _safe_unlink(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except (FileNotFoundError, OSError):
        pass


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
    os.replace(src, dst)

    if fsync:
        _fsync_dir_best_effort(dst.parent)
