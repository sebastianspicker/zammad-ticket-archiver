"""Shared exception types for managed configuration storage."""

from __future__ import annotations


class ManagedConfigError(ValueError):
    """Base error for safely reportable managed-configuration failures."""


class RevisionConflict(ManagedConfigError):
    """Signal an optimistic-concurrency conflict between configuration revisions."""


class _UnsafeManagedFile(ManagedConfigError):
    """Internal marker for a missing or unsafe managed-state file."""


class _MissingManagedFile(_UnsafeManagedFile):
    """Internal marker for an absent managed-state file."""


class _PostReplaceError(ManagedConfigError):
    """The target was replaced; callers must not roll back referenced state."""


class _PostReplaceDurabilityError(_PostReplaceError):
    """The target was replaced, but directory durability could not be confirmed."""


class _PostReplaceCleanupError(_PostReplaceError):
    """The target was durably replaced, but descriptor cleanup failed."""
