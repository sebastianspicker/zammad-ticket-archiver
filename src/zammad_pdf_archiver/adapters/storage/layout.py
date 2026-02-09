from __future__ import annotations

import re
from pathlib import Path

from zammad_pdf_archiver.domain.path_policy import (
    ensure_within_root,
    sanitize_segment,
    validate_segments,
)

_PREFIX_SPLIT_RE = re.compile(r"[>/]")


def _parse_prefix_segments(prefix: str) -> list[str]:
    if not isinstance(prefix, str) or not prefix.strip():
        raise ValueError("allow_prefixes entries must be non-empty strings")

    raw_parts = [p.strip() for p in _PREFIX_SPLIT_RE.split(prefix)]
    parts = [p for p in raw_parts if p]
    if not parts:
        raise ValueError("allow_prefixes entry produced no segments")
    return parts


def build_target_dir(
    root: Path,
    username: str,
    segments: list[str] | tuple[str, ...],
    *,
    allow_prefixes: list[str] | None = None,
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

    validate_segments([username], max_depth=1)
    validate_segments(list(segments))

    user_safe = sanitize_segment(username)
    segs_safe = [sanitize_segment(s) for s in segments]

    validate_segments([user_safe], max_depth=1)
    validate_segments(segs_safe)

    if allow_prefixes:
        allowed: list[list[str]] = []
        for prefix in allow_prefixes:
            prefix_parts = _parse_prefix_segments(prefix)
            validate_segments(prefix_parts)
            prefix_safe = [sanitize_segment(p) for p in prefix_parts]
            validate_segments(prefix_safe)
            allowed.append(prefix_safe)

        if not any(segs_safe[: len(prefix)] == prefix for prefix in allowed):
            raise ValueError("archive_path is not allowed by allow_prefixes policy")

    target = root / user_safe
    for seg in segs_safe:
        target = target / seg

    ensure_within_root(root, target)
    return target


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

    ticket_safe = sanitize_segment(str(ticket_number))
    ts_safe = sanitize_segment(timestamp_utc)

    try:
        rendered = pattern.format(
            ticket_number=ticket_safe,
            timestamp_utc=ts_safe,
            date_utc=ts_safe,
        )
    except KeyError as exc:
        raise ValueError(
            f"invalid filename_pattern format: unknown placeholder {exc.args[0]!r}"
        ) from exc
    except ValueError:
        # Re-raise ValueError as-is (e.g., from format specifier errors)
        raise
    except Exception as exc:
        raise ValueError(f"invalid filename_pattern format: {exc}") from exc

    rendered = rendered.strip()
    if not rendered:
        raise ValueError("filename_pattern produced an empty filename")

    # Disallow separators explicitly; patterns should not create directories.
    if "/" in rendered or "\\" in rendered or "\x00" in rendered:
        raise ValueError("filename_pattern must not include path separators or null bytes")

    validate_segments([rendered], max_depth=1, max_length=255)
    return rendered


def build_filename(
    ticket_number: int | str, date_iso: str, title_optional: str | None = None
) -> str:
    """
    Build a deterministic, filesystem-safe filename (no extension):
      <ticket>-<date>[-<title>]
    """
    ticket_safe = sanitize_segment(str(ticket_number))
    date_safe = sanitize_segment(date_iso)

    parts = [ticket_safe, date_safe]

    if title_optional:
        title_safe = sanitize_segment(title_optional)
        # Keep filenames bounded; callers can store full titles elsewhere.
        title_safe = title_safe[:80]
        if title_safe:
            parts.append(title_safe)

    # Avoid accidental empty/hidden names; ticket+date should make this non-empty.
    filename = "-".join([p for p in parts if p])
    return filename
