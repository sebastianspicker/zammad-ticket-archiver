"""Classify archive failures and calculate bounded retry delays."""

from __future__ import annotations

import errno

import httpx

from chronikwerk.archiving.error_messages import (
    ErrorMessages,
    format_fs_error,
    format_http_error,
)
from chronikwerk.failures import PermanentError, TransientError, wrap_exception

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
    (httpx.TimeoutException, ErrorMessages.HTTP_TIMEOUT),
    (httpx.RequestError, ErrorMessages.HTTP_REQUEST_ERROR),
)


def _classify_http_status(exc: httpx.HTTPStatusError) -> TransientError | PermanentError:
    status = exc.response.status_code
    if 500 <= status <= 599:
        return TransientError(format_http_error(status))
    if status in (401, 403):
        return PermanentError(format_http_error(status, is_auth=True))
    return PermanentError(format_http_error(status))


def _classify_os_error(exc: OSError) -> TransientError | PermanentError:
    err = exc.errno
    if isinstance(err, int) and err in _TRANSIENT_ERRNOS:
        return TransientError(format_fs_error(err, is_temporary=True))
    if isinstance(err, int) and err in _PERMANENT_ERRNOS:
        return PermanentError(format_fs_error(err, is_temporary=False))

    # Unknown OS errors default to permanent to avoid endless reprocessing loops.
    return PermanentError(ErrorMessages.FS_GENERIC_ERROR)


def _classify_httpx_error(exc: BaseException) -> TransientError | PermanentError | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return _classify_http_status(exc)
    for exc_type, message in _TRANSIENT_EXCEPTION_RULES:
        if isinstance(exc, exc_type):
            return TransientError(message)
    return None


def classify(exc: BaseException) -> TransientError | PermanentError:
    """
    Classify an exception into retryable (TransientError) vs non-retryable (PermanentError).

    Policy goals:
      - Predictable ticket state transitions (avoid accidental infinite retry loops).
      - Keep retryable failures retryable: network timeouts, upstream 5xx, rate limits,
        and certain filesystem errors commonly seen with network shares.
    """
    if isinstance(exc, TransientError | PermanentError):
        return exc

    httpx_result = _classify_httpx_error(exc)
    if httpx_result is not None:
        return httpx_result

    # Filesystem issues (local or network share).
    if isinstance(exc, OSError):
        return _classify_os_error(exc)

    # Validation/data issues (e.g. missing required ticket fields, path policy violations).
    if isinstance(exc, ValueError | TypeError):
        return PermanentError(str(exc) or exc.__class__.__name__)

    # Fail-safe default: stop automatic reprocessing unless explicitly classified transient.
    return wrap_exception(exc)
