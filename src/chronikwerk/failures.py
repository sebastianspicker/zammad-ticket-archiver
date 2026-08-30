"""Define shared retryable and permanent application failures."""

from __future__ import annotations

__all__ = ["TransientError", "PermanentError"]


class TransientError(Exception):
    """An error that is likely to succeed when retried (e.g. network issues)."""


class PermanentError(Exception):
    """An error that should not be retried automatically."""


def wrap_exception(exc: BaseException) -> TransientError | PermanentError:
    """
    Wrap an arbitrary exception in a domain error type.

    If the exception is already a domain error, it is returned as-is.
    Otherwise, it is wrapped as a PermanentError (fail-safe default) with the
    original exception attached as the cause.
    """
    if isinstance(exc, TransientError | PermanentError):
        return exc

    message = f"{exc.__class__.__name__}: {exc}".strip()
    wrapped = PermanentError(message or exc.__class__.__name__)
    wrapped.__cause__ = exc
    return wrapped
