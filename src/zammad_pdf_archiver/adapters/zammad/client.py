from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, NoReturn, TypeVar

import httpx
from pydantic import TypeAdapter, ValidationError

from zammad_pdf_archiver.adapters.http_util import timeouts_for
from zammad_pdf_archiver.adapters.zammad.errors import (
    AuthError,
    ClientError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from zammad_pdf_archiver.adapters.zammad.models import Article, TagList, Ticket

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class _RetryPolicy:
    # "retry up to 3 times" => 1 initial attempt + 3 retries = 4 total attempts.
    max_retries: int = 3
    backoff_base_seconds: float = 0.2

    def backoff_seconds(self, attempt: int) -> float:
        # attempt is 0-based for *retry count* (i.e., after the first failure).
        return self.backoff_base_seconds * (2**attempt)


class AsyncZammadClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        timeout_seconds: float = 10.0,
        verify_tls: bool = True,
        trust_env: bool = False,
        retry_policy: _RetryPolicy | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        url = httpx.URL(base_url)
        if not url.scheme or not url.host:
            raise ValueError("base_url must include scheme and host, e.g. https://zammad.example")

        # Ensure a trailing slash to make httpx base_url joining unambiguous.
        base_path = url.path.rstrip("/") + "/"
        self._base_url = url.copy_with(path=base_path)

        self._sleep = sleep
        self._retry = retry_policy or _RetryPolicy()

        self._owns_http_client = http_client is None
        self._http = http_client or httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Token token={api_token}",
                "Accept": "application/json",
            },
            timeout=timeouts_for(timeout_seconds),
            limits=httpx.Limits(
                max_connections=10,
                max_keepalive_connections=5,
                keepalive_expiry=30.0,
            ),
            verify=verify_tls,
            trust_env=trust_env,
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        if self._owns_http_client:
            await self._http.aclose()

    async def __aenter__(self) -> AsyncZammadClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        await self.aclose()

    async def get_ticket(self, ticket_id: int) -> Ticket:
        resp = await self._request_json("GET", f"api/v1/tickets/{ticket_id}")
        return Ticket.model_validate(resp)

    async def list_tags(self, ticket_id: int) -> TagList:
        resp = await self._request_json(
            "GET",
            "api/v1/tags",
            params={"object": "Ticket", "o_id": str(ticket_id)},
        )

        # Zammad may return either a raw JSON array or an object wrapper depending on version.
        if isinstance(resp, dict) and "tags" in resp:
            tags_value = resp["tags"]
        else:
            tags_value = resp

        try:
            tags = TypeAdapter(list[str]).validate_python(tags_value)
        except ValidationError as exc:
            raise ClientError(
                f"Zammad tags response format unexpected for ticket {ticket_id}: {exc!s}"
            ) from exc
        return TagList(tags)

    async def add_tag(self, ticket_id: int, tag: str) -> None:
        await self._request_json(
            "POST",
            "api/v1/tags/add",
            json={"object": "Ticket", "o_id": ticket_id, "item": tag},
        )

    async def remove_tag(self, ticket_id: int, tag: str) -> None:
        # Compatibility note: some Zammad deployments are strict about verb routing for tags.
        # Using POST keeps this client compatible with the documented `/tags/remove` endpoint.
        await self._request_json(
            "POST",
            "api/v1/tags/remove",
            json={"object": "Ticket", "o_id": ticket_id, "item": tag},
        )

    async def create_internal_article(
        self, ticket_id: int, subject: str, body_html: str
    ) -> Article:
        resp = await self._request_json(
            "POST",
            "api/v1/ticket_articles",
            json={
                "ticket_id": ticket_id,
                "subject": subject,
                "body": body_html,
                "content_type": "text/html",
                "internal": True,
            },
        )
        return Article.model_validate(resp)

    async def list_articles(self, ticket_id: int) -> list[Article]:
        resp = await self._request_json("GET", f"api/v1/ticket_articles/by_ticket/{ticket_id}")
        items = TypeAdapter(list[dict[str, Any]]).validate_python(resp)
        return [Article.model_validate(item) for item in items]

    async def get_attachment_content(
        self, ticket_id: int, article_id: int, attachment_id: int
    ) -> bytes:
        """Download attachment binary.
        GET /api/v1/ticket_attachment/{ticket}/{article}/{attachment}."""
        path = f"api/v1/ticket_attachment/{ticket_id}/{article_id}/{attachment_id}"
        response = await self._request("GET", path, headers={"Accept": "*/*"})
        return response.content

    async def _request_json(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any | None = None,
    ) -> Any:
        response = await self._request(method, path, params=params, json=json)
        try:
            return response.json()
        except ValueError as exc:  # pragma: no cover
            raise ClientError(
                "Invalid JSON from Zammad "
                f"(status={response.status_code}) at {response.request.url!s}"
            ) from exc

    async def _request(
        self,
        method: Literal["GET", "POST"],
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        # Total attempts = 1 initial + max_retries
        max_attempts = self._retry.max_retries + 1
        retry_count = 0

        while True:
            try:
                response = await self._http.request(
                    method, path, params=params, json=json, headers=headers
                )
            except httpx.TimeoutException as exc:
                retry_count = await self._retry_after_timeout_or_transport(
                    retry_count=retry_count,
                    max_attempts=max_attempts,
                    exc=exc,
                    timeout_path=path,
                )
                continue
            except httpx.TransportError as exc:
                retry_count = await self._retry_after_timeout_or_transport(
                    retry_count=retry_count,
                    max_attempts=max_attempts,
                    exc=exc,
                )
                continue

            retry_delay = self._retry_delay_for_response(
                response,
                retry_count=retry_count,
                max_attempts=max_attempts,
            )
            if retry_delay is not None:
                await self._sleep(retry_delay)
                retry_count += 1
                continue

            if 200 <= response.status_code < 300:
                return response

            self._raise_for_status(response)

    async def _retry_after_timeout_or_transport(
        self,
        *,
        retry_count: int,
        max_attempts: int,
        exc: Exception,
        timeout_path: str | None = None,
    ) -> int:
        if retry_count >= self._retry.max_retries:
            if isinstance(exc, httpx.TimeoutException):
                path = timeout_path or "<unknown>"
                raise ServerError(
                    f"Zammad API timeout after {max_attempts} attempts at {path}"
                ) from exc
            raise ServerError(f"Network error after {max_attempts} attempts") from exc
        await self._sleep(self._retry.backoff_seconds(retry_count))
        return retry_count + 1

    def _retry_delay_for_response(
        self,
        response: httpx.Response,
        *,
        retry_count: int,
        max_attempts: int,
    ) -> float | None:
        status = response.status_code
        if status >= 500:
            if retry_count >= self._retry.max_retries:
                raise ServerError(
                    f"Zammad server error (status={status}) after {max_attempts} attempts"
                )
            return self._retry.backoff_seconds(retry_count)
        if status == 429:
            if retry_count >= self._retry.max_retries:
                raise RateLimitError(
                    f"Zammad rate limit (status=429) after {max_attempts} attempts"
                )
            retry_after = _parse_retry_after_seconds(response.headers.get("Retry-After"))
            return retry_after or self._retry.backoff_seconds(retry_count)
        return None

    def _raise_for_status(self, response: httpx.Response) -> NoReturn:
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
    return seconds

