"""Typed source-capture failures, each carrying its Zammad note error code."""

from __future__ import annotations

from chronikwerk.failures import PermanentError, TransientError

__all__ = [
    "AttachmentTooLargeError",
    "InconsistentSourceError",
    "RecordClassMissingError",
    "TempSpaceExceededError",
]


class RecordClassMissingError(PermanentError):
    """The ticket has no record class, or one the retention policy does not know."""

    code = "record_class_missing"


class InconsistentSourceError(PermanentError):
    """The ticket kept changing while it was collected, for every allowed attempt."""

    code = "inconsistent_source"


class AttachmentTooLargeError(PermanentError):
    """One attachment exceeds the configured per-attachment byte limit."""

    code = "attachment_too_large"


class TempSpaceExceededError(TransientError):
    """Capture wrote more bytes into its temporary build area than allowed."""

    code = "temp_space_exceeded"
