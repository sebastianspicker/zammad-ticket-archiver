from __future__ import annotations


class ClientError(Exception):
    """Base class for Zammad API errors."""


class AuthError(ClientError):
    """Authentication/authorization failed (typically HTTP 401/403)."""


class NotFoundError(ClientError):
    """Requested resource was not found (HTTP 404)."""


class RateLimitError(ClientError):
    """Request was rate limited (HTTP 429)."""


class ServerError(ClientError):
    """Server-side failure or retry exhaustion (typically HTTP 5xx)."""

