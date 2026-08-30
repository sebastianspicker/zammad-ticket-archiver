"""Validate archive path segments to keep writes inside archive storage."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_ALLOWED_SEGMENT_RE = re.compile(r"[A-Za-z0-9._-]")
_WHITESPACE_RE = re.compile(r"\s+")
_MULTI_UNDERSCORE_RE = re.compile(r"_+")


def _asciiish_char(ch: str) -> str:
    if unicodedata.category(ch) == "Mn":
        return ""
    if ord(ch) < 128:
        return ch
    return "_"


def _safe_segment_char(ch: str) -> str:
    if _ALLOWED_SEGMENT_RE.fullmatch(ch) is not None:
        return ch
    return "_"


def sanitize_segment(seg: str) -> str:
    """
    Produce a filesystem-safe path segment.

    Policy (strict-but-usable and deterministic):
    - Unicode is normalized to NFKD and reduced to ASCII where possible.
    - Whitespace becomes "_".
    - Only [A-Za-z0-9._-] are kept.
    - All other characters are replaced with "_" (then consecutive "_" are collapsed).

    This function does not enforce length or reserved-segment rules; use validate_segments().
    """
    if not isinstance(seg, str):
        raise TypeError("seg must be str")

    # Normalize to a stable representation. Strip combining marks (so "ü" => "u"),
    # and replace other non-ASCII characters with "_" (so segments never become empty
    # just because they contain e.g. CJK/emoji).
    normalized = unicodedata.normalize("NFKD", seg)
    normalized = "".join(_asciiish_char(ch) for ch in normalized)

    normalized = _WHITESPACE_RE.sub("_", normalized)

    out = "".join(_safe_segment_char(ch) for ch in normalized)
    out = _MULTI_UNDERSCORE_RE.sub("_", out)
    if seg and not out:
        out = "_"
    return out


def validate_segments(
    segments: list[str] | tuple[str, ...],
    *,
    max_depth: int = 10,
    max_length: int = 64,
) -> list[str]:
    """Validate untrusted path segments before they reach filesystem layout code."""
    if max_depth <= 0:
        raise ValueError("max_depth must be > 0")
    if max_length <= 0:
        raise ValueError("max_length must be > 0")

    if len(segments) > max_depth:
        raise ValueError(f"too many path segments (max_depth={max_depth})")

    return [_validate_segment(seg, max_length=max_length) for seg in segments]


def _validate_segment(seg: str, *, max_length: int) -> str:
    """Reject segments that are empty, dot-only, contain separators/nulls, or exceed max_length."""
    if not isinstance(seg, str):
        raise TypeError("segments must be strings")
    if seg == "":
        raise ValueError("empty path segment is not allowed")
    if seg in {".", ".."}:
        raise ValueError("dot segments are not allowed")
    if "\x00" in seg:
        raise ValueError("null bytes are not allowed")
    if "/" in seg or "\\" in seg:
        raise ValueError("path separators are not allowed in segments")
    if len(seg) > max_length:
        raise ValueError(f"path segment too long (max_length={max_length})")
    return seg


def ensure_within_root(root: Path, target: Path) -> None:
    """Raise ValueError if target resolves outside of root (path-traversal guard)."""
    root_resolved = root.resolve(strict=False)
    target_resolved = target.resolve(strict=False)

    if not target_resolved.is_relative_to(root_resolved):
        raise ValueError("target path escapes root")
