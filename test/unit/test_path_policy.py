from __future__ import annotations

from pathlib import Path

import pytest

from zammad_pdf_archiver.adapters.storage.layout import build_target_dir
from zammad_pdf_archiver.domain.path_policy import (
    ensure_within_root,
    sanitize_segment,
    validate_segments,
)


def test_sanitize_segment_allows_safe_chars() -> None:
    assert sanitize_segment("Az09._-") == "Az09._-"


def test_sanitize_segment_replaces_whitespace() -> None:
    assert sanitize_segment("a b\tc\nd") == "a_b_c_d"


def test_sanitize_segment_normalizes_unicode() -> None:
    assert sanitize_segment("über café") == "uber_cafe"


def test_sanitize_segment_non_ascii_fallback() -> None:
    # Docs promise unsupported characters become "_" rather than producing empty segments.
    assert sanitize_segment("客户") == "_"
    assert sanitize_segment("🤷") == "_"


def test_unicode_homoglyph_traversal_segments_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    # NFKD can normalize fullwidth dots to ".."; validation must still block traversal semantics.
    with pytest.raises(ValueError, match="dot segments"):
        build_target_dir(root, "agent", ["．．"])


def test_sanitize_segment_collapses_multiple_underscores() -> None:
    assert sanitize_segment("a  b") == "a_b"
    assert sanitize_segment("a***b") == "a_b"


def test_sanitize_segment_rejects_non_string() -> None:
    with pytest.raises(TypeError, match="seg must be str"):
        sanitize_segment(123)  # type: ignore[arg-type]


def test_validate_segments_rejects_empty_and_dots() -> None:
    with pytest.raises(ValueError):
        validate_segments([""])
    with pytest.raises(ValueError):
        validate_segments(["."])
    with pytest.raises(ValueError):
        validate_segments([".."])


def test_validate_segments_rejects_separators_and_null_bytes() -> None:
    with pytest.raises(ValueError):
        validate_segments(["a/b"])
    with pytest.raises(ValueError):
        validate_segments([r"a\b"])
    with pytest.raises(ValueError):
        validate_segments(["a\x00b"])


def test_validate_segments_enforces_length() -> None:
    with pytest.raises(ValueError):
        validate_segments(["a" * 65], max_length=64)


def test_validate_segments_enforces_depth() -> None:
    with pytest.raises(ValueError):
        validate_segments(["a"] * 11, max_depth=10)


def test_validate_segments_rejects_non_string_values() -> None:
    with pytest.raises(TypeError, match="segments must be strings"):
        validate_segments(["a", 1])  # type: ignore[list-item]


def test_validate_segments_requires_positive_bounds() -> None:
    with pytest.raises(ValueError, match="max_depth must be > 0"):
        validate_segments(["a"], max_depth=0)
    with pytest.raises(ValueError, match="max_length must be > 0"):
        validate_segments(["a"], max_length=0)


def test_ensure_within_root_rejects_escape() -> None:
    root = Path("/var/archive")
    target = root / ".." / "etc"
    with pytest.raises(ValueError):
        ensure_within_root(root, target)


def test_ensure_within_root_allows_normalized_paths_within_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    target = root / "a" / ".." / "b"
    ensure_within_root(root, target)


def test_build_target_dir_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    user_dir = root / "agent"
    user_dir.mkdir()
    link = user_dir / "A"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not supported in this environment")

    with pytest.raises(ValueError, match="escapes root"):
        build_target_dir(root, "agent", ["A", "B"])
