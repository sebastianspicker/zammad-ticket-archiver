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
        return TransientError(f"HTTP {status} from upstream")  # noqa: EM101
    if isinstance(status, int) and status in (401, 403):
        return PermanentError(f"HTTP {status} (auth/permission) from upstream")  # noqa: EM101
    if isinstance(status, int):
        return PermanentError(f"HTTP {status} from upstream")  # noqa: EM101
    return PermanentError("HTTP error from upstream")  # noqa: EM101


def _classify_os_error(exc: OSError) -> TransientError | PermanentError:
    err = getattr(exc, "errno", None)
    if isinstance(err, int) and err in _TRANSIENT_ERRNOS:
        return TransientError(f"Temporary filesystem error (errno={err})")  # noqa: EM101
    if isinstance(err, int) and err in _PERMANENT_ERRNOS:
        return PermanentError(f"Filesystem policy/permission error (errno={err})")  # noqa: EM101

    # Unknown OS errors default to permanent to avoid endless reprocessing loops.
    return PermanentError("Filesystem error")  # noqa: EM101


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
        return TransientError("HTTP timeout")  # noqa: EM101
    if isinstance(exc, httpx.RequestError):
        return TransientError("HTTP connection/request error")  # noqa: EM101
    if isinstance(exc, httpx.HTTPStatusError):
        return _classify_http_status(exc)

    # Zammad API exceptions (already normalized by the client).
    if isinstance(exc, (ServerError, RateLimitError)):
        return TransientError(str(exc) or "Zammad transient error")  # noqa: EM101
    if isinstance(exc, (AuthError, NotFoundError)):
        return PermanentError(str(exc) or "Zammad permanent error")  # noqa: EM101
    if isinstance(exc, ClientError):
        # Includes validation/path policy issues surfaced via 4xx responses.
        return PermanentError(str(exc) or "Zammad client error")  # noqa: EM101

    # Filesystem issues (local or network share).
    if isinstance(exc, OSError):
        return _classify_os_error(exc)

    # Validation/data issues (e.g. missing required ticket fields, path policy violations).
    if isinstance(exc, (ValueError, TypeError)):
        return PermanentError(str(exc) or exc.__class__.__name__)  # noqa: EM101

    # Fail-safe default: stop automatic reprocessing unless explicitly classified transient.
    return wrap_exception(exc)

