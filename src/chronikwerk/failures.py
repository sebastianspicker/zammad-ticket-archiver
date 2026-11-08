"""Define shared retryable and permanent application failures."""

from __future__ import annotations

__all__ = ["TransientError", "PermanentError"]


class TransientError(Exception):
    """An error that is likely to succeed when retried (e.g. network issues)."""


class PermanentError(Exception):
    """An error that should not be retried automatically."""
