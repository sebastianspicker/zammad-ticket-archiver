from __future__ import annotations

import errno

import httpx

from zammad_pdf_archiver.adapters.zammad.errors import (
    AuthError,
    ClientError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from zammad_pdf_archiver.domain.errors import PermanentError, TransientError, wrap_exception
from zammad_pdf_archiver.domain.error_messages import (
    ErrorMessages,
    format_http_error,
    format_fs_error,
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


def _classify_http_status(exc: httpx.HTTPStatusError) -> TransientError | PermanentError:
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int) and 500 <= status <= 599:
        return TransientError(format_http_error(status))
    if isinstance(status, int) and status in (401, 403):
        return PermanentError(format_http_error(status, is_auth=True))
    if isinstance(status, int):
        return PermanentError(format_http_error(status))
    return PermanentError(ErrorMessages.HTTP_REQUEST_ERROR)


def _classify_os_error(exc: OSError) -> TransientError | PermanentError:
    err = getattr(exc, "errno", None)
    if isinstance(err, int) and err in _TRANSIENT_ERRNOS:
        return TransientError(format_fs_error(err, is_temporary=True))
    if isinstance(err, int) and err in _PERMANENT_ERRNOS:
        return PermanentError(format_fs_error(err, is_temporary=False))

    # Unknown OS errors default to permanent to avoid endless reprocessing loops.
    return PermanentError(ErrorMessages.FS_GENERIC_ERROR)


def classify(exc: BaseException) -> TransientError | PermanentError:
    """
    Classify an exception into retryable (TransientError) vs non-retryable (PermanentError).

    Policy goals:
      - Predictable ticket state transitions (avoid accidental infinite retry loops).
      - Keep retryable failures retryable: network timeouts, upstream 5xx, rate limits,
        and certain filesystem errors commonly seen with network shares.
    """
    if isinstance(exc, (TransientError, PermanentError)):
        return exc

    # httpx network & timeout errors.
    if isinstance(exc, httpx.TimeoutException):
        return TransientError(ErrorMessages.HTTP_TIMEOUT)
    if isinstance(exc, httpx.RequestError):
        return TransientError(ErrorMessages.HTTP_REQUEST_ERROR)
    if isinstance(exc, httpx.HTTPStatusError):
        return _classify_http_status(exc)

    # Zammad API exceptions (already normalized by the client).
    if isinstance(exc, (ServerError, RateLimitError)):
        return TransientError(str(exc) or ErrorMessages.ZAMMAD_TRANSIENT_ERROR)
    if isinstance(exc, (AuthError, NotFoundError)):
        return PermanentError(str(exc) or ErrorMessages.ZAMMAD_PERMANENT_ERROR)
    if isinstance(exc, ClientError):
        # Includes validation/path policy issues surfaced via 4xx responses.
        return PermanentError(str(exc) or ErrorMessages.ZAMMAD_CLIENT_ERROR)

    # Filesystem issues (local or network share).
    if isinstance(exc, OSError):
        return _classify_os_error(exc)

    # Validation/data issues (e.g. missing required ticket fields, path policy violations).
    if isinstance(exc, (ValueError, TypeError)):
        return PermanentError(str(exc) or exc.__class__.__name__)

    # Fail-safe default: stop automatic reprocessing unless explicitly classified transient.
    return wrap_exception(exc)

