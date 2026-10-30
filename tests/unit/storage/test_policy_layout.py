"""Verify externally visible archive path and filename policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from chronikwerk.storage.layout import build_target_dir
from chronikwerk.storage.policy import ensure_within_root, sanitize_segment, validate_segments


def test_storage_layout_sanitizes_without_colliding_and_stays_under_root(tmp_path: Path) -> None:
    first = build_target_dir(tmp_path, "Müller Team", ["2026", "Dossier"])
    second = build_target_dir(tmp_path, "Muller Team", ["2026", "Dossier"])

    assert first != second
    assert first.is_relative_to(tmp_path)
    assert first.parts[-2:] == ("2026", "Dossier")
    assert "Muller_Team" in first.parts[-3]
    assert sanitize_segment("Müller Team/東京") == "Muller_Team_"


def test_storage_policy_rejects_escape_hatches(tmp_path: Path) -> None:
    assert validate_segments(["safe", "segment"]) == ["safe", "segment"]

    for unsafe in ("", ".", "..", "nested/path", "nested\\path", "bad\x00name"):
        with pytest.raises(ValueError):
            validate_segments([unsafe])
    with pytest.raises(ValueError, match="escapes root"):
        ensure_within_root(tmp_path, tmp_path.parent / "outside")
