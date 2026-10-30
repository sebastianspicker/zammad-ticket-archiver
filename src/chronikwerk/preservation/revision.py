"""Revision identifiers and the object-key prefix of one archive package."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

from chronikwerk.failures import PermanentError

__all__ = [
    "RECORD_CLASS_PATTERN",
    "REVISION_ID_PATTERN",
    "PackageLayoutError",
    "new_revision_id",
    "revision_directory_name",
    "revision_key_prefix",
]

RECORD_CLASS_PATTERN = re.compile(r"^[a-z0-9-]{1,64}$")
_TICKET_NUMBER_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
REVISION_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_ARCHIVE_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_MAX_SEGMENT_CHARS = 128
_MAX_REVISION_NUMBER = 9999


class PackageLayoutError(PermanentError):
    """A package path, key segment or revision number violates the package layout."""


def new_revision_id() -> str:
    """Return a fresh time-ordered revision identifier (UUIDv7)."""
    return str(uuid.uuid7())


def revision_directory_name(revision_number: int, revision_id: str) -> str:
    """Return the ``r<NNNN>_<revision_id>`` directory name of one revision."""
    if isinstance(revision_number, bool) or not 1 <= revision_number <= _MAX_REVISION_NUMBER:
        raise PackageLayoutError(f"revision number must be 1..{_MAX_REVISION_NUMBER}")
    if not REVISION_ID_PATTERN.fullmatch(revision_id):
        raise PackageLayoutError("revision id must match [A-Za-z0-9-]{1,64}")
    return f"r{revision_number:04d}_{revision_id}"


def _validate_segment(label: str, value: str) -> str:
    """Return one key segment unchanged or raise when it is unsafe."""
    if not value or value in {".", ".."}:
        raise PackageLayoutError(f"{label} must be a non-empty, non-relative segment")
    if any(char in value for char in ("/", "\\", "\x00")):
        raise PackageLayoutError(f"{label} must not contain '/', '\\' or NUL")
    if len(value) > _MAX_SEGMENT_CHARS:
        raise PackageLayoutError(f"{label} exceeds {_MAX_SEGMENT_CHARS} characters")
    return value


def _validate_archive_segment(label: str, value: str) -> str:
    """Return one archive user or path segment, restricted to the path-policy charset."""
    _validate_segment(label, value)
    if not _ARCHIVE_SEGMENT_PATTERN.fullmatch(value):
        raise PackageLayoutError(f"{label} must match [A-Za-z0-9._-]{{1,128}}")
    return value


def revision_key_prefix(
    *,
    record_class: str,
    archive_user: str,
    archive_path: Sequence[str],
    ticket_number: str,
    revision_number: int,
    revision_id: str,
) -> str:
    """Return the store key prefix (with trailing slash) of one revision package."""
    if not RECORD_CLASS_PATTERN.fullmatch(record_class):
        raise PackageLayoutError("record class must match [a-z0-9-]{1,64}")
    _validate_segment("ticket number", ticket_number)
    if not _TICKET_NUMBER_PATTERN.fullmatch(ticket_number):
        raise PackageLayoutError("ticket number must match [A-Za-z0-9-]+")
    segments = [
        record_class,
        _validate_archive_segment("archive user", archive_user),
        *(_validate_archive_segment("archive path segment", segment) for segment in archive_path),
        f"Ticket-{ticket_number}",
        revision_directory_name(revision_number, revision_id),
    ]
    return "/".join(segments) + "/"
