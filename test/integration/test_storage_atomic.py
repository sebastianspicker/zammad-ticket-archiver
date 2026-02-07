from __future__ import annotations

from pathlib import Path

import pytest

from zammad_pdf_archiver.adapters.storage import write_atomic_bytes
from zammad_pdf_archiver.adapters.storage.fs_storage import write_bytes


def _tmp_files(dir_path: Path) -> list[Path]:
    return [p for p in dir_path.iterdir() if p.is_file() and p.name.startswith(".tmp-")]


def test_write_atomic_bytes_creates_dirs_and_writes_contents(tmp_path: Path) -> None:
    target = tmp_path / "a" / "b" / "payload.bin"
    data = b"\x00hello\xff"

    write_atomic_bytes(target, data, storage_root=tmp_path)

    assert target.exists()
    assert target.read_bytes() == data
    assert _tmp_files(target.parent) == []


def test_write_atomic_bytes_overwrites_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "payload.bin"
    target.write_bytes(b"old")

    data = b"new-data"
    write_atomic_bytes(target, data, storage_root=tmp_path)

    assert target.read_bytes() == data
    assert _tmp_files(tmp_path) == []


def test_write_atomic_bytes_cleans_up_temp_on_exception(tmp_path: Path) -> None:
    target_dir = tmp_path / "target-dir"
    target_dir.mkdir()

    with pytest.raises(OSError):
        write_atomic_bytes(target_dir, b"data", storage_root=tmp_path)

    assert _tmp_files(tmp_path) == []


def test_storage_writes_reject_paths_outside_storage_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    target = outside / "payload.bin"
    with pytest.raises(ValueError, match="escapes root"):
        write_atomic_bytes(target, b"x", storage_root=root)
    with pytest.raises(ValueError, match="escapes root"):
        write_bytes(target, b"x", storage_root=root)


def test_storage_writes_reject_symlink_traversal_under_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    link = root / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not supported in this environment")

    target = link / "payload.bin"
    with pytest.raises(ValueError, match="symlink|escapes root"):
        write_atomic_bytes(target, b"x", storage_root=root)
