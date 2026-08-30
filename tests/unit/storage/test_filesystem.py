"""Verify atomic filesystem writes remain confined to the archive root."""

from __future__ import annotations

from pathlib import Path

import pytest

from chronikwerk.storage.filesystem import write_bytes


def test_write_bytes_is_atomic_restrictive_and_confined(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "payload.bin"
    write_bytes(target, b"first", storage_root=tmp_path, fsync=False)
    write_bytes(target, b"second", storage_root=tmp_path, fsync=False)

    assert target.read_bytes() == b"second"
    assert target.stat().st_mode & 0o777 == 0o640
    assert not list(target.parent.glob(".tmp-*"))
    with pytest.raises(ValueError, match="escapes root"):
        write_bytes(tmp_path.parent / "outside.bin", b"x", storage_root=tmp_path)
