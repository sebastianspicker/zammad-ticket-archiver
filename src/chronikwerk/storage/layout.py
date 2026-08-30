"""Derive safe archive filesystem paths and filenames from validated ticket metadata."""

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


def _render_filename_pattern(pattern: str, *, ticket_safe: str, ts_safe: str) -> str:
    try:
        return pattern.format(
            ticket_number=ticket_safe,
            timestamp_utc=ts_safe,
            date_utc=ts_safe,
        )
    except KeyError as exc:
        raise ValueError(
            f"invalid filename_pattern format: unknown placeholder {exc.args[0]!r}"
        ) from exc
    except (IndexError, TypeError) as exc:
        raise ValueError(f"invalid filename_pattern format: {exc}") from exc


def _validate_rendered_filename(rendered: str) -> str:
    rendered = rendered.strip()
    if not rendered:
        raise ValueError("filename_pattern produced an empty filename")
    if rendered in (".", ".."):
        raise ValueError("filename must not be '.' or '..'")
    if "/" in rendered or "\\" in rendered or "\x00" in rendered:
        raise ValueError("filename_pattern must not include path separators or null bytes")
    validate_segments([rendered], max_depth=1, max_length=255)
    return rendered


def build_filename_from_pattern(
    pattern: str,
    *,
    ticket_number: int | str,
    timestamp_utc: str,
) -> str:
    """
    Render a deterministic, filesystem-safe filename from a format string.

    Supported placeholders:
      - {ticket_number}
      - {timestamp_utc} (kept date-only for stability: YYYY-MM-DD)
      - {date_utc}      (alias for {timestamp_utc})

    The rendered filename is validated to be a single safe path segment.
    """
    if not isinstance(pattern, str) or not pattern.strip():
        raise ValueError("pattern must be a non-empty string")

    ticket_safe = _collision_safe_segment(str(ticket_number))
    ts_safe = _collision_safe_segment(timestamp_utc)

    rendered = _render_filename_pattern(pattern, ticket_safe=ticket_safe, ts_safe=ts_safe)
    return _validate_rendered_filename(rendered)
