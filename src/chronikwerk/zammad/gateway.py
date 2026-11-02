"""Fetch Zammad ticket resources with bounded retries and typed failures."""

# DECISION: Governed by docs/adr/0006-zammad-outbound-transport-trust-boundary.md.
# Preserve the configured connection boundary and private test-runtime exception.

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from pydantic import TypeAdapter, ValidationError

from chronikwerk.configuration.zammad import ZammadConnection
from chronikwerk.zammad import transport
from chronikwerk.zammad.dto import Article, TagList
from chronikwerk.zammad.errors import ClientError, NotFoundError
from chronikwerk.zammad.transport import ZammadRequest


def trigger_tag_query(tag: str) -> str:
    """Build the ticket search query for an exact tag, escaping backslashes and quotes."""
    escaped = tag.replace("\\", "\\\\").replace('"', '\\"')
    return f'tags:"{escaped}"'


class AsyncZammadClient:
    """
    Async HTTP client for the Zammad REST API with retry and error mapping.

    ``runtime`` is the injection point for transport runtime options (retry policy, sleep,
    HTTP client, private-network allowance); production composition leaves it unset.
    """

    def __init__(
        self,
        *,
        connection: ZammadConnection,
        runtime: transport.ZammadRuntimeOptions | None = None,
    ) -> None:
        url = httpx.URL(connection.origin)
        if not url.scheme or not url.host:
            raise ValueError("base_url must include scheme and host, e.g. https://zammad.example")

        # Ensure a trailing slash to make httpx base_url joining unambiguous.
        base_path = url.path.rstrip("/") + "/"
        self._base_url = url.copy_with(path=base_path)

        self._current_user_login: str | None = None
        runtime = runtime or transport.ZammadRuntimeOptions()
        self._transport = transport.ZammadTransport(
            transport.ZammadTransportOptions(
                base_url=self._base_url,
                policy_url=connection.origin,
                api_token=connection.api_token.get_secret_value(),
                timeout_seconds=connection.timeout_seconds,
                verify_tls=True,
                trust_env=connection.trust_environment,
                allow_insecure_http=connection.allow_insecure_http,
                allow_private_networks=connection.allow_private_origin,
                max_response_body_bytes=transport.MAX_RESPONSE_BODY_BYTES,
            ),
            runtime,
        )

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

    async def get_ticket_raw(self, ticket_id: int, *, max_bytes: int) -> bytes:
        """Fetch the verbatim ``expand=true`` ticket body, bounded by ``max_bytes``."""
        return await self._transport.request_bytes(
            "GET", f"api/v1/tickets/{ticket_id}", params={"expand": "true"}, max_bytes=max_bytes
        )

    async def get_current_user_login(self) -> str:
        """Return the login of the API token's user, fetched once per client."""
        if self._current_user_login is None:
            resp = await self._request_json(ZammadRequest("GET", "api/v1/users/me"))
            login = resp.get("login") if isinstance(resp, dict) else None
            if not isinstance(login, str) or not login.strip():
                # Without its own login the service could not exclude itself as current_agent.
                raise ClientError("Zammad users/me response has no login for the API token")
            self._current_user_login = login.strip()
        return self._current_user_login

    async def list_tags(self, ticket_id: int) -> TagList:
        """Fetch all tags for a ticket."""
        resp = await self._request_json(
            ZammadRequest(
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
            ZammadRequest(
                "POST",
                "api/v1/tags/add",
                json={"object": "Ticket", "o_id": ticket_id, "item": tag},
            )
        )

    async def remove_tag(self, ticket_id: int, tag: str) -> None:
        """Remove a tag from a ticket (idempotent)."""
        # Zammad routes `/tags/remove` with DELETE only and reads the JSON body.
        await self._request_json(
            ZammadRequest(
                "DELETE",
                "api/v1/tags/remove",
                json={"object": "Ticket", "o_id": ticket_id, "item": tag},
            )
        )

    async def create_internal_article(
        self, ticket_id: int, subject: str, body_html: str
    ) -> Article:
        """Create an internal (non-customer-visible) article on a ticket."""
        resp = await self._request_json(
            ZammadRequest(
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

    async def list_articles_raw(self, ticket_id: int, *, max_bytes: int) -> bytes:
        """Fetch the verbatim by-ticket article list body, bounded by ``max_bytes``."""
        return await self._transport.request_bytes(
            "GET", f"api/v1/ticket_articles/by_ticket/{ticket_id}", max_bytes=max_bytes
        )

    async def get_article_plain(
        self, article_id: int, *, max_bytes: int, timeout_seconds: float
    ) -> bytes | None:
        """Fetch an email article's original RFC 5322 message, or ``None`` when absent.

        ``timeout_seconds`` bounds the whole download; per-read timeouts alone allow a slow drip.
        """
        try:
            return await self._transport.request_bytes(
                "GET",
                f"api/v1/ticket_article_plain/{article_id}",
                max_bytes=max_bytes,
                timeout_seconds=timeout_seconds,
            )
        except NotFoundError:
            return None

    async def download_attachment(
        self,
        ticket_id: int,
        article_id: int,
        attachment_id: int,
        *,
        target: Path,
        max_bytes: int,
        timeout_seconds: float,
    ) -> int:
        """Stream one attachment into the new file ``target`` and return the bytes written."""
        return await self._transport.stream_to_file(
            f"api/v1/ticket_attachment/{ticket_id}/{article_id}/{attachment_id}",
            target=target,
            max_bytes=max_bytes,
            timeout_seconds=timeout_seconds,
        )

    async def search_ticket_ids(self, query: str, *, limit: int = 100, page: int = 1) -> list[int]:
        """Return one page of ticket IDs matching a Zammad search query."""
        resp = await self._request_json(
            ZammadRequest(
                "GET",
                "api/v1/tickets/search",
                params={"query": query, "limit": str(limit), "page": str(page)},
            )
        )
        # Zammad answers with {"tickets": [ids], ...} or a list of ticket objects.
        items = resp.get("tickets") if isinstance(resp, dict) else resp
        if not isinstance(items, list):
            raise ClientError("Zammad ticket search response format unexpected")
        ids: list[int] = []
        for item in items:
            ticket_id = item.get("id") if isinstance(item, dict) else item
            if isinstance(ticket_id, bool) or not isinstance(ticket_id, int):
                raise ClientError("Zammad ticket search response contains a non-integer ticket id")
            ids.append(ticket_id)
        return ids

    async def _request_json(self, request: ZammadRequest) -> Any:
        return await self._transport.request_json(
            request.method,
            request.path,
            params=request.params,
            json=request.json,
            max_retries=request.max_retries,
        )
