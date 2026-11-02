"""Pin archive path confinement, depth limits, and collision-safe names."""

from __future__ import annotations

from pathlib import Path

import pytest

from chronikwerk.storage.filesystem import (
    move_file_within_root,
    path_entry_exists,
)
from chronikwerk.storage.layout import build_target_dir


def test_move_rejects_destination_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    source = root / "a.bin"
    source.write_bytes(b"x")

    with pytest.raises(ValueError, match="escapes root"):
        move_file_within_root(source, tmp_path / "b.bin", storage_root=root)

    assert source.exists()


def test_path_entry_exists_does_not_follow_leaf_symlink(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "dangling").symlink_to(tmp_path / "nowhere")
    except OSError:
        pytest.skip("symlinks are not supported in this environment")

    assert path_entry_exists(root / "dangling", storage_root=root) is True
    assert path_entry_exists(root / "absent", storage_root=root) is False


@pytest.mark.parametrize("unsafe", ["..", ".", "a/b", "a\\b", "a\x00b", ""])
def test_target_dir_rejects_unsafe_segments(tmp_path: Path, unsafe: str) -> None:
    with pytest.raises(ValueError):
        build_target_dir(tmp_path, "user", ["2026", unsafe])


@pytest.mark.parametrize("unsafe", ["..", "a/b", "a\\b", "a\x00b", ""])
def test_target_dir_rejects_unsafe_username(tmp_path: Path, unsafe: str) -> None:
    with pytest.raises(ValueError):
        build_target_dir(tmp_path, unsafe, ["2026"])


def test_target_dir_allows_ten_segments_and_rejects_eleven(tmp_path: Path) -> None:
    ten = [f"s{index}" for index in range(10)]

    target = build_target_dir(tmp_path, "user", ten)

    assert target == tmp_path.joinpath("user", *ten)
    with pytest.raises(ValueError, match="too many path segments"):
        build_target_dir(tmp_path, "user", [*ten, "s10"])


def test_target_dir_rejects_overlong_segment(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="too long"):
        build_target_dir(tmp_path, "user", ["x" * 65])


def test_clean_segments_are_kept_verbatim(tmp_path: Path) -> None:
    assert build_target_dir(tmp_path, "alice", ["2026", "Dossier_1"]) == (
        tmp_path / "alice" / "2026" / "Dossier_1"
    )


def test_sanitized_segment_gets_sha256_prefix_suffix(tmp_path: Path) -> None:
    target = build_target_dir(tmp_path, "user", ["Müller Team"])

    # sha256("Müller Team".encode("utf-8")).hexdigest()[:32] == e71f884f...
    assert target.name == "Muller_Team-e71f884fc9d8c48fe8244a10d312564c"


def test_colliding_raw_names_stay_distinct(tmp_path: Path) -> None:
    spaced = build_target_dir(tmp_path, "user", ["AB 12"])
    underscored = build_target_dir(tmp_path, "user", ["AB_12"])

    assert underscored.name == "AB_12"
    assert spaced.name == "AB_12-972bc8a6eac1ac13d28b02d997d33c2d"
