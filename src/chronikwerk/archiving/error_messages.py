"""Error strings used across archive retry classification and reporting."""

from __future__ import annotations


class ErrorMessages:
    """Centralize localized, safe error messages exposed by adapters."""

    # HTTP/Network errors
    HTTP_TIMEOUT = "HTTP timeout"
    HTTP_REQUEST_ERROR = "HTTP connection/request error"
    HTTP_UPSTREAM_ERROR = "HTTP {status} from upstream"
    HTTP_AUTH_ERROR = "HTTP {status} (auth/permission) from upstream"

    # Filesystem errors
    FS_TEMPORARY_ERROR = "Temporary filesystem error (errno={errno})"
    FS_POLICY_ERROR = "Filesystem policy/permission error (errno={errno})"
    FS_GENERIC_ERROR = "Filesystem error"

    # Zammad errors
    ZAMMAD_TRANSIENT_ERROR = "Zammad transient error"
    ZAMMAD_PERMANENT_ERROR = "Zammad permanent error"
    ZAMMAD_CLIENT_ERROR = "Zammad client error"

    # Processing errors
    PROCESSING_CANCELLED = "Processing cancelled"
    VALIDATION_ERROR = "Validation error"
    CONFIGURATION_ERROR = "Configuration error"


def format_http_error(status: int | None, is_auth: bool = False) -> str:
    """Format an HTTP failure without exposing sensitive response content."""
    if status is None:
        return ErrorMessages.HTTP_REQUEST_ERROR

    if is_auth:
        return ErrorMessages.HTTP_AUTH_ERROR.format(status=status)

    return ErrorMessages.HTTP_UPSTREAM_ERROR.format(status=status)


def format_fs_error(errno: int | None, is_temporary: bool = False) -> str:
    """Format a filesystem failure without leaking host-specific details."""
    if errno is None:
        return ErrorMessages.FS_GENERIC_ERROR

    if is_temporary:
        return ErrorMessages.FS_TEMPORARY_ERROR.format(errno=errno)

    return ErrorMessages.FS_POLICY_ERROR.format(errno=errno)
