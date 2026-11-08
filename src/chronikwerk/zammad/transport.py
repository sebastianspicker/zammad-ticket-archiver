"""Execute bounded, policy-checked requests to the configured Zammad origin."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, NoReturn

import httpx

from chronikwerk.outbound import (
    OutboundPolicyPermanentError,
    OutboundPolicyTransientError,
    ResponseBodyTooLargeError,
    UnsupportedResponseEncodingError,
    buffered_response,
    pin_request_url,
    preflight_response_body_limit,
    read_response_body_limited,
    timeouts_for,
    validate_url_policy,
    validate_url_policy_async,
)
from chronikwerk.zammad.errors import (
    AuthError,
    ClientError,
    NotFoundError,
    RateLimitError,
    ResponseTooLargeError,
    ServerError,
)

MAX_RESPONSE_BODY_BYTES = 32 * 1024 * 1024
_STREAM_CHUNK_BYTES = 64 * 1024
_BINARY_ACCEPT = {"Accept": "*/*"}
# O_EXCL: never overwrite an existing path; O_NOFOLLOW: never write through a planted symlink.
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC

_ResponseConsumer = Callable[[httpx.Response], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bound retry attempts and exponential backoff for retryable Zammad failures."""

    # "retry up to 3 times" => 1 initial attempt + 3 retries = 4 total attempts.
    max_retries: int = 3
    backoff_base_seconds: float = 0.2

    def backoff_seconds(self, attempt: int) -> float:
        """Return the delay for a zero-based retry count after the initial attempt."""
        return self.backoff_base_seconds * (2**attempt)


@dataclass(frozen=True, slots=True)
class ZammadRuntimeOptions:
    """Inject retry policy, sleep, HTTP client, and network allowance into the transport."""

    retry_policy: RetryPolicy | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    http_client: httpx.AsyncClient | None = None
    allow_private_networks: bool = False


@dataclass(frozen=True, slots=True)
class ZammadTransportOptions:
    """Describe the validated origin, credentials, and limits of one transport."""

    base_url: httpx.URL
    policy_url: str
    api_token: str
    timeout_seconds: float
    verify_tls: bool
    trust_env: bool
    allow_insecure_http: bool
    allow_private_networks: bool
    max_response_body_bytes: int = MAX_RESPONSE_BODY_BYTES


@dataclass(frozen=True, slots=True)
class _RequestAttempt:
    retry_count: int
    max_attempts: int
    max_retries: int


@dataclass(frozen=True, slots=True)
class ZammadRequest:
    """One JSON request delegated to the Zammad transport."""

    method: Literal["GET", "POST", "DELETE"]
    path: str
    params: dict[str, str] | None = None
    json: Any | None = None
    headers: dict[str, str] | None = None
    max_retries: int | None = None


@dataclass(frozen=True, slots=True)
class _RetryFailure:
    exception: Exception
    timeout_path: str | None = None


class ZammadTransport:
    """Execute bounded, policy-checked JSON requests against one Zammad origin."""

    def __init__(
        self,
        options: ZammadTransportOptions,
        runtime: ZammadRuntimeOptions,
    ) -> None:
        self._base_url = options.base_url
        self._sleep = runtime.sleep
        self._retry = runtime.retry_policy or RetryPolicy()
        self._dns_timeout_seconds = min(5.0, float(options.timeout_seconds))
        self._allow_insecure_http = options.allow_insecure_http
        # An injected runtime may explicitly opt into private test fixtures;
        # production-owned clients use the safe constructor default.
        self._allow_private_networks = (
            options.allow_private_networks or runtime.allow_private_networks
        )
        self._max_response_body_bytes = options.max_response_body_bytes
        try:
            validate_url_policy(
                options.policy_url,
                allow_insecure_http=options.allow_insecure_http,
                allow_private_networks=self._allow_private_networks,
            )
        except OutboundPolicyPermanentError as exc:
            raise ClientError(str(exc)) from exc
        self._owns_http_client = runtime.http_client is None
        self._http = runtime.http_client or httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Token token={options.api_token}",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
            timeout=timeouts_for(options.timeout_seconds),
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30.0,
            ),
            verify=options.verify_tls,
            trust_env=options.trust_env,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        """Close the underlying HTTP client when this transport created it."""
        if self._owns_http_client:
            await self._http.aclose()

    async def request_json(
        self,
        method: Literal["GET", "POST", "DELETE"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any | None = None,
        max_retries: int | None = None,
    ) -> Any:
        """Send one request, retrying transient failures, and return the decoded JSON body."""
        response: httpx.Response = await self._request(
            ZammadRequest(
                method=method,
                path=path,
                params=params,
                json=json,
                max_retries=max_retries,
            ),
            self._buffer_success_response,
        )
        try:
            return response.json()
        except ValueError as exc:  # pragma: no cover
            raise ClientError(
                "Invalid JSON from Zammad "
                f"(status={response.status_code}) at {response.request.url!s}"
            ) from exc

    async def request_bytes(
        self,
        method: Literal["GET", "POST", "DELETE"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        max_bytes: int,
        timeout_seconds: float | None = None,
    ) -> bytes:
        """Send one request with the shared retry policy; bound the body by ``max_bytes``.

        ``timeout_seconds`` is an optional total deadline over all attempts; exceeding it is a
        transient ``ServerError``.
        """

        async def consume(response: httpx.Response) -> bytes:
            return await self._read_limited(response, max_bytes=max_bytes)

        request = ZammadRequest(method, path, params=params, headers=_BINARY_ACCEPT)
        if timeout_seconds is None:
            result: bytes = await self._request(request, consume)
            return result
        result = await self._request_within(request, consume, timeout_seconds=timeout_seconds)
        return result

    async def stream_to_file(
        self,
        path: str,
        *,
        target: Path,
        max_bytes: int,
        timeout_seconds: float,
        params: dict[str, str] | None = None,
    ) -> int:
        """
        Stream a GET response into the new file ``target`` and return the bytes written.

        The file is created exclusively. Any failure, including exceeding ``max_bytes`` or the
        total ``timeout_seconds``, removes only the partial file this call created.
        """

        async def consume(response: httpx.Response) -> int:
            return await self._write_limited(response, target=target, max_bytes=max_bytes)

        request = ZammadRequest("GET", path, params=params, headers=_BINARY_ACCEPT)
        written: int = await self._request_within(request, consume, timeout_seconds=timeout_seconds)
        return written

    async def _request_within(
        self,
        request: ZammadRequest,
        consume: _ResponseConsumer,
        *,
        timeout_seconds: float,
    ) -> Any:
        """Run ``_request`` under a total deadline; a timeout becomes a transient ServerError."""
        try:
            async with asyncio.timeout(timeout_seconds):
                return await self._request(request, consume)
        except TimeoutError as exc:
            raise ServerError(
                f"Zammad download timed out after {timeout_seconds} seconds at {request.path}"
            ) from exc

    async def _write_limited(
        self,
        response: httpx.Response,
        *,
        target: Path,
        max_bytes: int,
    ) -> int:
        self._preflight(response, max_bytes=max_bytes)
        written = 0
        created: tuple[int, int] | None = None
        descriptor = os.open(target, _CREATE_FLAGS, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                status = os.fstat(handle.fileno())
                created = (status.st_dev, status.st_ino)
                async for chunk in response.aiter_bytes(chunk_size=_STREAM_CHUNK_BYTES):
                    written += len(chunk)
                    if written > max_bytes:
                        raise self._too_large(response, max_bytes)
                    await asyncio.to_thread(handle.write, chunk)
        except BaseException:
            if created is not None:
                _remove_if_created(target, created)
            raise
        return written

    async def _request(
        self,
        request: ZammadRequest,
        consume: _ResponseConsumer,
    ) -> Any:
        requested_retries = request.max_retries
        retries = (
            self._retry.max_retries if requested_retries is None else max(0, requested_retries)
        )
        max_attempts = retries + 1
        retry_count = 0

        while True:
            try:
                response, retry_delay = await self._request_once(
                    request,
                    consume,
                    attempt=_RequestAttempt(
                        retry_count=retry_count,
                        max_attempts=max_attempts,
                        max_retries=retries,
                    ),
                )
            except httpx.TimeoutException as exc:
                retry_count = await self._retry_after_timeout_or_transport(
                    _RequestAttempt(
                        retry_count=retry_count,
                        max_attempts=max_attempts,
                        max_retries=retries,
                    ),
                    _RetryFailure(exception=exc, timeout_path=request.path),
                )
                continue
            except httpx.TransportError as exc:
                retry_count = await self._retry_after_timeout_or_transport(
                    _RequestAttempt(
                        retry_count=retry_count,
                        max_attempts=max_attempts,
                        max_retries=retries,
                    ),
                    _RetryFailure(exception=exc),
                )
                continue

            if retry_delay is not None:
                await self._sleep(retry_delay)
                retry_count += 1
                continue
            return response

    async def _request_once(
        self,
        request: ZammadRequest,
        consume: _ResponseConsumer,
        *,
        attempt: _RequestAttempt,
    ) -> tuple[Any, float | None]:
        try:
            resolved_address = await validate_url_policy_async(
                str(self._base_url),
                allow_insecure_http=self._allow_insecure_http,
                allow_private_networks=self._allow_private_networks,
                timeout_seconds=self._dns_timeout_seconds,
                attempt=attempt.retry_count,
            )
        except OutboundPolicyPermanentError as exc:
            raise ClientError(str(exc)) from exc
        except OutboundPolicyTransientError as exc:
            raise ServerError(str(exc)) from exc
        request_url, pin_headers, extensions = pin_request_url(
            self._base_url.join(request.path),
            resolved_address,
        )
        request_headers = {**(request.headers or {}), **pin_headers}
        async with self._http.stream(
            request.method,
            request_url,
            params=request.params,
            json=request.json,
            headers=request_headers or None,
            extensions=extensions or None,
        ) as streamed_response:
            retry_delay = self._retry_delay_for_response(
                streamed_response,
                attempt,
            )
            if retry_delay is not None:
                return None, retry_delay
            if 200 <= streamed_response.status_code < 300:
                return await consume(streamed_response), None
            self.raise_for_status(streamed_response)

    async def _buffer_success_response(self, response: httpx.Response) -> httpx.Response:
        content = await self._read_limited(response, max_bytes=self._max_response_body_bytes)
        return buffered_response(response, content)

    async def _read_limited(self, response: httpx.Response, *, max_bytes: int) -> bytes:
        try:
            return await read_response_body_limited(response, max_bytes=max_bytes)
        except ResponseBodyTooLargeError as exc:
            raise self._too_large(response, max_bytes) from exc
        except UnsupportedResponseEncodingError as exc:
            raise self._compressed(response) from exc

    def _preflight(self, response: httpx.Response, *, max_bytes: int) -> None:
        try:
            preflight_response_body_limit(response, max_bytes=max_bytes)
        except ResponseBodyTooLargeError as exc:
            raise self._too_large(response, max_bytes) from exc
        except UnsupportedResponseEncodingError as exc:
            raise self._compressed(response) from exc

    @staticmethod
    def _too_large(response: httpx.Response, max_bytes: int) -> ResponseTooLargeError:
        return ResponseTooLargeError(
            f"Zammad response body exceeded the {max_bytes}-byte limit "
            f"(status={response.status_code}) at {response.request.url!s}",
            limit=max_bytes,
        )

    @staticmethod
    def _compressed(response: httpx.Response) -> ClientError:
        return ClientError(
            "Zammad returned a compressed response despite Accept-Encoding: identity "
            f"(status={response.status_code}) at {response.request.url!s}"
        )

    async def _retry_after_timeout_or_transport(
        self,
        attempt: _RequestAttempt,
        failure: _RetryFailure,
    ) -> int:
        if attempt.retry_count >= attempt.max_retries:
            if isinstance(failure.exception, httpx.TimeoutException):
                path = failure.timeout_path or "<unknown>"
                raise ServerError(
                    f"Zammad API timeout after {attempt.max_attempts} attempts at {path}"
                ) from failure.exception
            raise ServerError(
                f"Network error after {attempt.max_attempts} attempts"
            ) from failure.exception
        await self._sleep(self._retry.backoff_seconds(attempt.retry_count))
        return attempt.retry_count + 1

    def _retry_delay_for_response(
        self,
        response: httpx.Response,
        attempt: _RequestAttempt,
    ) -> float | None:
        status = response.status_code
        if status >= 500:
            if attempt.retry_count >= attempt.max_retries:
                raise ServerError(
                    f"Zammad server error (status={status}) after {attempt.max_attempts} attempts"
                )
            return self._retry.backoff_seconds(attempt.retry_count)
        if status == 429:
            if attempt.retry_count >= attempt.max_retries:
                raise RateLimitError(
                    f"Zammad rate limit (status=429) after {attempt.max_attempts} attempts"
                )
            retry_after = _parse_retry_after_seconds(response.headers.get("Retry-After"))
            return retry_after or self._retry.backoff_seconds(attempt.retry_count)
        return None

    @staticmethod
    def raise_for_status(response: httpx.Response) -> NoReturn:
        """Map an unsuccessful HTTP response to the typed Zammad failure."""
        status = response.status_code
        url = str(response.request.url)

        if status in (401, 403):
            raise AuthError(f"Zammad auth failed (status={status}) at {url}")
        if status == 404:
            raise NotFoundError(f"Zammad resource not found (status=404) at {url}")
        if status == 429:
            raise RateLimitError(f"Zammad rate limit (status=429) at {url}")
        if status >= 500:
            raise ServerError(f"Zammad server error (status={status}) at {url}")
        if status >= 400:
            raise ClientError(f"Zammad client error (status={status}) at {url}")

        raise ClientError(f"Unexpected Zammad HTTP status={status} at {url}")


def _remove_if_created(target: Path, created: tuple[int, int]) -> None:
    """Unlink ``target`` only while it is still the file this transport created."""
    try:
        current = os.lstat(target)
    except FileNotFoundError:
        return
    if (current.st_dev, current.st_ino) == created:
        target.unlink(missing_ok=True)


def _parse_retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    if seconds < 0:
        return None
    return min(seconds, 60)
