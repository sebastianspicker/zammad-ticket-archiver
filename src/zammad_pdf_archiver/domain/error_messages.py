"""Error message constants for consistent error handling.

This module centralizes error messages to:
- Ensure consistency across the codebase
- Enable easier i18n support in the future
- Reduce code duplication
"""
from __future__ import annotations


class ErrorMessages:
    """Centralized error message constants."""
    
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


class ErrorCodes:
    """Error code constants for programmatic handling."""
    
    # Network errors
    HTTP_TIMEOUT = "E_HTTP_TIMEOUT"
    HTTP_CONNECTION = "E_HTTP_CONN"
    HTTP_5XX = "E_HTTP_5XX"
    HTTP_4XX = "E_HTTP_4XX"
    HTTP_AUTH = "E_HTTP_AUTH"
    
    # Filesystem errors
    FS_TEMPORARY = "E_FS_TEMP"
    FS_PERMISSION = "E_FS_PERM"
    FS_POLICY = "E_FS_POLICY"
    
    # Zammad errors
    ZAMMAD_AUTH = "E_ZAMMAD_AUTH"
    ZAMMAD_NOT_FOUND = "E_ZAMMAD_NOTFOUND"
    ZAMMAD_RATE_LIMIT = "E_ZAMMAD_RATELIMIT"
    ZAMMAD_SERVER = "E_ZAMMAD_SERVER"
    
    # Processing errors
    VALIDATION = "E_VALIDATION"
    CONFIGURATION = "E_CONFIG"
    CANCELLED = "E_CANCELLED"


def format_http_error(status: int | None, is_auth: bool = False) -> str:
    """Format HTTP error message with status code.
    
    Args:
        status: HTTP status code
        is_auth: Whether this is an auth-related error
        
    Returns:
        Formatted error message
    """
    if status is None:
        return ErrorMessages.HTTP_REQUEST_ERROR
    
    if is_auth:
        return ErrorMessages.HTTP_AUTH_ERROR.format(status=status)
    
    return ErrorMessages.HTTP_UPSTREAM_ERROR.format(status=status)


def format_fs_error(errno: int | None, is_temporary: bool = False) -> str:
    """Format filesystem error message with errno.
    
    Args:
        errno: System errno value
        is_temporary: Whether this is a temporary error
        
    Returns:
        Formatted error message
    """
    if errno is None:
        return ErrorMessages.FS_GENERIC_ERROR
    
    if is_temporary:
        return ErrorMessages.FS_TEMPORARY_ERROR.format(errno=errno)
    
    return ErrorMessages.FS_POLICY_ERROR.format(errno=errno)
