"""Derive safe archive directory segments from validated ticket metadata."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from chronikwerk.storage.policy import (
    ensure_within_root,
    sanitize_segment,
    validate_segments,
)

_DISAMBIGUATION_HEX_LENGTH = 32
_MAX_PATH_SEGMENT_LENGTH = 64
_DISAMBIGUATED_SUFFIX_RE = re.compile(rf"-[0-9a-f]{{{_DISAMBIGUATION_HEX_LENGTH}}}$")


def _collision_safe_segment(raw: str) -> str:
    safe = sanitize_segment(raw)
    if safe in {".", ".."} or not raw:
        return safe
    if safe == raw and _DISAMBIGUATED_SUFFIX_RE.search(safe) is None:
        return safe

    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_DISAMBIGUATION_HEX_LENGTH]
    prefix_length = _MAX_PATH_SEGMENT_LENGTH - len(digest) - 1
    return f"{safe[:prefix_length]}-{digest}"


def _target_from_segments(root: Path, user_safe: str, segs_safe: list[str]) -> Path:
    target = root / user_safe
    for seg in segs_safe:
        target = target / seg
    return target


def build_target_dir(
    root: Path,
    username: str,
    segments: list[str] | tuple[str, ...],
) -> Path:
    """
    Build a deterministic directory path:
      ROOT / <sanitized-user> / <sanitized-segments...>

    This performs validation on raw inputs (rejects separators, dot segments, null bytes),
    then sanitizes segments for filesystem safety, then validates the sanitized output and
    ensures the final target is within ROOT.
    """
    if not isinstance(root, Path):
        root = Path(root)

    raw_segments = list(segments)
    validate_segments([username], max_depth=1)
    validate_segments(raw_segments)

    user_safe = _collision_safe_segment(username)
    segs_safe = [_collision_safe_segment(s) for s in raw_segments]

    validate_segments([user_safe], max_depth=1)
    validate_segments(segs_safe)

    target = _target_from_segments(root, user_safe, segs_safe)
    ensure_within_root(root, target)
    return target
