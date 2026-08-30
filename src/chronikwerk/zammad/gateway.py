"""Fetch Zammad ticket resources with bounded retries and typed failures."""

# DECISION: Governed by docs/adr/0006-zammad-outbound-transport-trust-boundary.md.
# Preserve the configured connection boundary and private test-runtime exception.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, NoReturn

import httpx
from pydantic import TypeAdapter, ValidationError

from chronikwerk.configuration.zammad import ZammadConnection
from chronikwerk.zammad import transport
from chronikwerk.zammad.dto import Article, TagList, Ticket
from chronikwerk.zammad.errors import ClientError


@dataclass(frozen=True, slots=True)
class _JsonRequest:
    """One JSON request delegated to the Zammad transport."""

    method: Literal["GET", "POST"]
    path: str
    params: dict[str, str] | None = None
    json: Any | None = None
    max_retries: int | None = None


class AsyncZammadClient:
    """Async HTTP client for the Zammad REST API with retry and error mapping."""

    def __init__(
        self,
        *,
        connection: ZammadConnection,
        _runtime: transport._ZammadRuntimeOptions | None = None,
    ) -> None:
        url = httpx.URL(connection.origin)
        if not url.scheme or not url.host:
            raise ValueError("base_url must include scheme and host, e.g. https://zammad.example")

        # Ensure a trailing slash to make httpx base_url joining unambiguous.
        base_path = url.path.rstrip("/") + "/"
        self._base_url = url.copy_with(path=base_path)

        runtime = _runtime or transport._ZammadRuntimeOptions()
        self._transport = transport._ZammadTransport(
            transport._ZammadTransportOptions(
                base_url=self._base_url,
                policy_url=connection.origin,
                api_token=connection.api_token.get_secret_value(),
                timeout_seconds=connection.timeout_seconds,
                verify_tls=True,
                trust_env=connection.trust_environment,
                allow_insecure_http=connection.allow_insecure_http,
                allow_private_networks=connection.allow_private_origin,
                max_response_body_bytes=transport._MAX_RESPONSE_BODY_BYTES,
            ),
            runtime,
        )

    @property
    def _dns_timeout_seconds(self) -> float:
        return self._transport.dns_timeout_seconds

    @property
    def _allow_insecure_http(self) -> bool:
        return self._transport.allow_insecure_http

    @property
    def _allow_private_networks(self) -> bool:
        return self._transport.allow_private_networks

    @property
    def _http(self) -> httpx.AsyncClient:
        return self._transport.http_client

    async def aclose(self) -> None:
        """Close the underlying HTTP client if it was created by this instance."""
        await self._transport.aclose()

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
        """Fetch a single ticket by ID."""
        resp = await self._request_json(_JsonRequest("GET", f"api/v1/tickets/{ticket_id}"))
        return Ticket.model_validate(resp)

    async def list_tags(self, ticket_id: int) -> TagList:
        """Fetch all tags for a ticket."""
        resp = await self._request_json(
            _JsonRequest(
                "GET",
                "api/v1/tags",
                params={"object": "Ticket", "o_id": str(ticket_id)},
            )
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
        """Add a tag to a ticket (idempotent)."""
        await self._request_json(
            _JsonRequest(
                "POST",
                "api/v1/tags/add",
                json={"object": "Ticket", "o_id": ticket_id, "item": tag},
            )
        )

    async def remove_tag(self, ticket_id: int, tag: str) -> None:
        """Remove a tag from a ticket (idempotent)."""
        # Using POST keeps this client compatible with the documented `/tags/remove` endpoint.
        await self._request_json(
            _JsonRequest(
                "POST",
                "api/v1/tags/remove",
                json={"object": "Ticket", "o_id": ticket_id, "item": tag},
            )
        )

    async def create_internal_article(
        self, ticket_id: int, subject: str, body_html: str
    ) -> Article:
        """Create an internal (non-customer-visible) article on a ticket."""
        resp = await self._request_json(
            _JsonRequest(
                "POST",
                "api/v1/ticket_articles",
                json={
                    "ticket_id": ticket_id,
                    "subject": subject,
                    "body": body_html,
                    "content_type": "text/html",
                    "internal": True,
                },
                max_retries=0,
            )
        )
        return Article.model_validate(resp)

    async def list_articles(self, ticket_id: int) -> list[Article]:
        """List all articles belonging to a ticket."""
        resp = await self._request_json(
            _JsonRequest("GET", f"api/v1/ticket_articles/by_ticket/{ticket_id}")
        )
        items = TypeAdapter(list[dict[str, Any]]).validate_python(resp)
        return [Article.model_validate(item) for item in items]

    async def _request_json(self, request: _JsonRequest) -> Any:
        return await self._transport.request_json(
            request.method,
            request.path,
            params=request.params,
            json=request.json,
            max_retries=request.max_retries,
        )

    def _raise_for_status(self, response: httpx.Response) -> NoReturn:
        self._transport.raise_for_status(response)
