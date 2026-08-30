"""Execute bounded, policy-checked requests to the configured Zammad origin."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, NoReturn

import httpx

from chronikwerk.outbound import (
    OutboundPolicyPermanentError,
    OutboundPolicyTransientError,
    validate_url_policy,
    validate_url_policy_async,
)
from chronikwerk.zammad.errors import (
    AuthError,
    ClientError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from chronikwerk.zammad.http import (
    ResponseBodyTooLargeError,
    UnsupportedResponseEncodingError,
    buffered_response,
    pin_request_url,
    read_response_body_limited,
    timeouts_for,
)

_MAX_RESPONSE_BODY_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class _RetryPolicy:
    # "retry up to 3 times" => 1 initial attempt + 3 retries = 4 total attempts.
    max_retries: int = 3
    backoff_base_seconds: float = 0.2

    def backoff_seconds(self, attempt: int) -> float:
        """Return the delay for a zero-based retry count after the initial attempt."""
        return self.backoff_base_seconds * (2**attempt)


@dataclass(frozen=True, slots=True)
class _ZammadRuntimeOptions:
    retry_policy: _RetryPolicy | None = None
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    http_client: httpx.AsyncClient | None = None
    allow_private_networks: bool = False


@dataclass(frozen=True, slots=True)
class _ZammadTransportOptions:
    base_url: httpx.URL
    policy_url: str
    api_token: str
    timeout_seconds: float
    verify_tls: bool
    trust_env: bool
    allow_insecure_http: bool
    allow_private_networks: bool
    max_response_body_bytes: int = _MAX_RESPONSE_BODY_BYTES


@dataclass(frozen=True, slots=True)
class _RequestAttempt:
    retry_count: int
    max_attempts: int
    max_retries: int


@dataclass(frozen=True, slots=True)
class _ZammadRequest:
    method: Literal["GET", "POST"]
    path: str
    params: dict[str, str] | None = None
    json: Any | None = None
    headers: dict[str, str] | None = None
    max_retries: int | None = None


@dataclass(frozen=True, slots=True)
class _RetryFailure:
    exception: Exception
    timeout_path: str | None = None


class _ZammadTransport:
    def __init__(
        self,
        options: _ZammadTransportOptions,
        runtime: _ZammadRuntimeOptions,
    ) -> None:
        self._base_url = options.base_url
        self._sleep = runtime.sleep
        self._retry = runtime.retry_policy or _RetryPolicy()
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

    @property
    def dns_timeout_seconds(self) -> float:
        return self._dns_timeout_seconds

    @property
    def allow_insecure_http(self) -> bool:
        return self._allow_insecure_http

    @property
    def allow_private_networks(self) -> bool:
        return self._allow_private_networks

    @property
    def http_client(self) -> httpx.AsyncClient:
        return self._http

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def request_json(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any | None = None,
        max_retries: int | None = None,
    ) -> Any:
        response = await self._request(
            _ZammadRequest(
                method=method,
                path=path,
                params=params,
                json=json,
                max_retries=max_retries,
            )
        )
        try:
            return response.json()
        except ValueError as exc:  # pragma: no cover
            raise ClientError(
                "Invalid JSON from Zammad "
                f"(status={response.status_code}) at {response.request.url!s}"
            ) from exc

    async def _request(
        self,
        request: _ZammadRequest,
    ) -> httpx.Response:
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
            if response is not None:
                return response

    async def _request_once(
        self,
        request: _ZammadRequest,
        *,
        attempt: _RequestAttempt,
    ) -> tuple[httpx.Response | None, float | None]:
        try:
            resolved_address = await validate_url_policy_async(
                str(self._base_url),
                allow_insecure_http=self._allow_insecure_http,
                allow_private_networks=self._allow_private_networks,
                timeout_seconds=self._dns_timeout_seconds,
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
                return await self._buffer_success_response(streamed_response), None
            self.raise_for_status(streamed_response)

    async def _buffer_success_response(self, response: httpx.Response) -> httpx.Response:
        try:
            content = await read_response_body_limited(
                response,
                max_bytes=self._max_response_body_bytes,
            )
        except ResponseBodyTooLargeError as exc:
            raise ClientError(
                "Zammad response body exceeded the "
                f"{self._max_response_body_bytes}-byte limit "
                f"(status={response.status_code}) at {response.request.url!s}"
            ) from exc
        except UnsupportedResponseEncodingError as exc:
            raise ClientError(
                "Zammad returned a compressed response despite "
                "Accept-Encoding: identity "
                f"(status={response.status_code}) at {response.request.url!s}"
            ) from exc
        return buffered_response(response, content)

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
