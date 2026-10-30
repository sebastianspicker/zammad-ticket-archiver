"""Typed commit-protocol failures, each carrying its Zammad note error code."""

from __future__ import annotations

from chronikwerk.failures import PermanentError, TransientError

__all__ = [
    "CommitInterruptedError",
    "SealCheckFailedError",
    "StoreConflictError",
    "StoreWriteFailedError",
    "TimestampCheckFailedError",
    "TimestampUnavailableError",
    "VerificationFailedError",
]


class SealCheckFailedError(PermanentError):
    """The manifest could not be sealed, or the seal failed the pre-completion check."""

    code = "seal_failed"


class TimestampCheckFailedError(PermanentError):
    """The timestamp token is invalid, untrusted or outside the collection window."""

    code = "timestamp_failed"


class TimestampUnavailableError(TransientError):
    """The TSA could not be reached; the same capture may be committed again later."""

    code = "timestamp_failed"


class StoreWriteFailedError(TransientError):
    """The archive store refused or failed a write while the revision was unpublished."""

    code = "store_write_failed"


class StoreConflictError(PermanentError):
    """The store already holds a different object or revision under the same key."""

    code = "store_conflict"


class VerificationFailedError(PermanentError):
    """What the store returned on read-back differs from the sealed manifest."""

    code = "verification_failed"


class CommitInterruptedError(TransientError):
    """A restart interrupted the commit before the seal files reached the store."""

    code = "commit_interrupted"
