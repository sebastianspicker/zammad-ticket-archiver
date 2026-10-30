"""Classify archive failures as transient or permanent and format safe failure messages."""

from __future__ import annotations

import errno
import sqlite3
from collections.abc import Mapping
from typing import Literal

import httpx

from chronikwerk.failures import PermanentError, TransientError

_HTTP_TIMEOUT = "HTTP timeout"
_HTTP_REQUEST_ERROR = "HTTP connection/request error"
_FS_GENERIC_ERROR = "Filesystem error"
_DATABASE_BUSY = "Database temporarily unavailable"

# Error codes of the preservation workflow (brief section 5.10) and their retry class. Each
# typed error (capture_errors, commit_errors, PackageTooLargeError) carries its code as a
# ``code`` class attribute and subclasses the matching TransientError/PermanentError.
ERROR_CODE_CLASSES: Mapping[str, Literal["transient", "permanent"]] = {
    "record_class_missing": "permanent",
    "inconsistent_source": "permanent",
    "attachment_too_large": "permanent",
    "package_too_large": "permanent",
    "temp_space_exceeded": "transient",
    "seal_failed": "permanent",
    "timestamp_failed": "transient",  # permanent when the token itself is invalid
    "validation_unavailable": "transient",
    "store_write_failed": "transient",
    "store_conflict": "permanent",
    "verification_failed": "permanent",
    "retention_not_confirmed": "permanent",
    "commit_interrupted": "transient",
    "not_triggered": "permanent",
    "internal_error": "transient",
}
# Codes for failures that carry no preservation code (Zammad, HTTP, filesystem, ticket fields).
TRANSIENT_ERROR_CODE = "transient_error"
PERMANENT_ERROR_CODE = "permanent_error"
# Code for exceptions no policy rule knows; transient, so a bug does not drop the request.
INTERNAL_ERROR_CODE = "internal_error"
_KNOWN_FAILURES = (
    TransientError,
    PermanentError,
    OSError,
    ValueError,
    TypeError,
    httpx.HTTPError,
    sqlite3.OperationalError,
)

_TRANSIENT_ERRNOS: set[int] = {
    # Temporary / retryable.
    errno.EAGAIN,
    getattr(errno, "EWOULDBLOCK", errno.EAGAIN),
    errno.ETIMEDOUT,
    # Common network share / remote FS flakiness.
    errno.ECONNRESET,
    errno.EPIPE,
    getattr(errno, "ENOTCONN", 107),
    getattr(errno, "ESTALE", 116),
    errno.EIO,
    # Infrastructure/outage style issues that can resolve without changing inputs.
    getattr(errno, "ENETDOWN", 100),
    getattr(errno, "ENETUNREACH", 101),
    getattr(errno, "EHOSTUNREACH", 113),
    # Environment can be fixed by ops (mount, capacity).
    errno.ENOENT,
    errno.ENOSPC,
    getattr(errno, "EDQUOT", 122),
    getattr(errno, "EROFS", 30),
}

_PERMANENT_ERRNOS: set[int] = {
    errno.EACCES,
    errno.EPERM,
    errno.EINVAL,
    errno.ENAMETOOLONG,
    errno.ENOTDIR,
    errno.EISDIR,
}

_TRANSIENT_EXCEPTION_RULES: tuple[tuple[type[BaseException], str], ...] = (
    (httpx.TimeoutException, _HTTP_TIMEOUT),
    (httpx.RequestError, _HTTP_REQUEST_ERROR),
)


def _format_http_error(status: int, *, is_auth: bool = False) -> str:
    """Format an HTTP failure without exposing sensitive response content."""
    if is_auth:
        return f"HTTP {status} (auth/permission) from upstream"
    return f"HTTP {status} from upstream"


def _format_fs_error(err: int, *, is_temporary: bool) -> str:
    """Format a filesystem failure without leaking host-specific details."""
    if is_temporary:
        return f"Temporary filesystem error (errno={err})"
    return f"Filesystem policy/permission error (errno={err})"


def _classify_http_status(exc: httpx.HTTPStatusError) -> TransientError | PermanentError:
    status = exc.response.status_code
    if 500 <= status <= 599:
        return TransientError(_format_http_error(status))
    if status in (401, 403):
        return PermanentError(_format_http_error(status, is_auth=True))
    return PermanentError(_format_http_error(status))


def _classify_os_error(exc: OSError) -> TransientError | PermanentError:
    err = exc.errno
    if isinstance(err, int) and err in _TRANSIENT_ERRNOS:
        return TransientError(_format_fs_error(err, is_temporary=True))
    if isinstance(err, int) and err in _PERMANENT_ERRNOS:
        return PermanentError(_format_fs_error(err, is_temporary=False))

    # Unknown OS errors default to permanent to avoid endless reprocessing loops.
    return PermanentError(_FS_GENERIC_ERROR)


def describe_os_error(exc: OSError) -> str:
    """Return the classified, host-neutral summary of a filesystem failure (no paths)."""
    return str(_classify_os_error(exc))


def is_known_failure(exc: BaseException) -> bool:
    """Report whether an explicit policy rule (not the fallback) classifies ``exc``."""
    return isinstance(exc, _KNOWN_FAILURES)


def _classify_httpx_error(exc: BaseException) -> TransientError | PermanentError | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return _classify_http_status(exc)
    for exc_type, message in _TRANSIENT_EXCEPTION_RULES:
        if isinstance(exc, exc_type):
            return TransientError(message)
    return None


def error_code(exc: BaseException) -> str | None:
    """Return the preservation error code an exception carries, or ``None``."""
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and code in ERROR_CODE_CLASSES else None


def failure_code(exc: BaseException, classified: TransientError | PermanentError) -> str:
    """Return the journal error code of a classified failure.

    A carried preservation code wins; exceptions no policy rule knows get ``internal_error``
    (transient, like its ``ERROR_CODE_CLASSES`` entry); otherwise the code is
    ``transient_error`` or ``permanent_error``.
    """
    code = error_code(exc)
    if code is not None:
        return code
    if not is_known_failure(exc):
        return INTERNAL_ERROR_CODE
    if isinstance(classified, TransientError):
        return TRANSIENT_ERROR_CODE
    return PERMANENT_ERROR_CODE


def classify(exc: BaseException) -> TransientError | PermanentError:
    """
    Classify an exception into retryable (TransientError) vs non-retryable (PermanentError).

    Always returns a classification; unknown exceptions are wrapped as transient
    (``internal_error``), so the journal's bounded back-off retries them.

    Policy goals:
      - Predictable ticket state transitions; retries stay bounded by the journal.
      - Keep retryable failures retryable: network timeouts, upstream 5xx, rate limits,
        busy databases and certain filesystem errors commonly seen with network shares.
    """
    if isinstance(exc, TransientError | PermanentError):
        return exc

    httpx_result = _classify_httpx_error(exc)
    if httpx_result is not None:
        return httpx_result

    # Filesystem issues (local or network share).
    if isinstance(exc, OSError):
        return _classify_os_error(exc)

    # A locked or busy SQLite database (journal, catalog) clears without changing inputs.
    if isinstance(exc, sqlite3.OperationalError):
        return TransientError(_DATABASE_BUSY)

    # Validation/data issues (e.g. missing required ticket fields, path policy violations).
    if isinstance(exc, ValueError | TypeError):
        return PermanentError(str(exc) or exc.__class__.__name__)

    # Unknown failures retry with the journal's bounded back-off (ERROR_CODE_CLASSES).
    wrapped = TransientError(exc.__class__.__name__)
    wrapped.__cause__ = exc
    return wrapped
