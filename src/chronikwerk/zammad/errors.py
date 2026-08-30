"""Represent failures returned by the Zammad transport boundary."""

from __future__ import annotations

from chronikwerk.failures import PermanentError, TransientError


class ClientError(PermanentError):
    """Base class for non-retryable Zammad API errors."""


class AuthError(ClientError):
    """Authentication/authorization failed (typically HTTP 401/403)."""


class NotFoundError(ClientError):
    """Requested resource was not found (HTTP 404)."""


class RateLimitError(ClientError, TransientError):
    """Request was rate limited (HTTP 429)."""


class ServerError(ClientError, TransientError):
    """Server-side failure or retry exhaustion (typically HTTP 5xx)."""
