"""Pin the exact HTTP wire contract of the Zammad gateway through its public client methods."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from chronikwerk.configuration.zammad import ZammadConnection
from chronikwerk.zammad import transport
from chronikwerk.zammad.errors import (
    AuthError,
    ClientError,
    NotFoundError,
    RateLimitError,
    ServerError,
)
from chronikwerk.zammad.gateway import AsyncZammadClient

_ORIGIN = "https://zammad.example"
_AUTH = "Token token=token-value"


def _connection() -> ZammadConnection:
    """Build a connection whose hostname is never resolved through DNS."""
    return ZammadConnection(
        origin=_ORIGIN,
        api_token=SecretStr("token-value"),
        allow_private_origin=True,
    )


def _fast_client() -> tuple[AsyncZammadClient, list[float]]:
    """Build a client with a recording no-op sleep injected through the runtime options."""
    delays: list[float] = []

    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    runtime = transport.ZammadRuntimeOptions(sleep=sleep)
    return AsyncZammadClient(connection=_connection(), runtime=runtime), delays


def _run(call: Any) -> Any:
    """Run one client coroutine factory on a fresh client and close the client."""

    async def scenario() -> Any:
        async with AsyncZammadClient(connection=_connection()) as client:
            return await call(client)

    return asyncio.run(scenario())


def _only_call(route: respx.Route) -> httpx.Request:
    """Return the single request recorded by a route."""
    assert route.call_count == 1
    return route.calls.last.request


def test_get_ticket_uses_get_with_token_auth_and_expand_query() -> None:
    """get_ticket issues GET /api/v1/tickets/{id}?expand=true with token authorization."""
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{_ORIGIN}/api/v1/tickets/123").respond(
            200, json={"id": 123, "number": "123", "title": "T"}
        )
        ticket = _run(lambda c: c.get_ticket(123))
    request = _only_call(route)
    assert ticket.id == 123
    assert request.method == "GET"
    assert request.url.path == "/api/v1/tickets/123"
    assert request.url.query == b"expand=true"
    assert request.headers["authorization"] == _AUTH
    assert request.headers["accept"] == "application/json"
    assert request.headers["accept-encoding"] == "identity"


def test_list_tags_queries_object_and_id_and_accepts_both_shapes() -> None:
    """list_tags sends object=Ticket&o_id=N and accepts a bare array or a tags wrapper."""
    for body in (["a", "b"], {"tags": ["a", "b"]}):
        with respx.mock(assert_all_called=True) as mock:
            route = mock.get(f"{_ORIGIN}/api/v1/tags").respond(200, json=body)
            tags = _run(lambda c: c.list_tags(123))
        request = _only_call(route)
        assert tags.root == ["a", "b"]
        assert request.method == "GET"
        assert request.url.path == "/api/v1/tags"
        assert dict(request.url.params) == {"object": "Ticket", "o_id": "123"}
        assert request.headers["authorization"] == _AUTH


def test_list_tags_rejects_non_string_entries_with_client_error() -> None:
    """A malformed tags payload surfaces as ClientError rather than a validation error."""
    with respx.mock() as mock:
        mock.get(f"{_ORIGIN}/api/v1/tags").respond(200, json=[1, 2])
        with pytest.raises(ClientError, match="tags response format unexpected"):
            _run(lambda c: c.list_tags(5))


@pytest.mark.parametrize(
    ("method_name", "verb", "path"),
    [
        ("add_tag", "POST", "/api/v1/tags/add"),
        # Zammad routes tags/remove with `via: :delete` only (config/routes/tag.rb).
        ("remove_tag", "DELETE", "/api/v1/tags/remove"),
    ],
)
def test_tag_mutations_send_item_object_and_id(method_name: str, verb: str, path: str) -> None:
    """add_tag POSTs and remove_tag DELETEs the documented JSON body to their own endpoints."""
    with respx.mock(assert_all_called=True) as mock:
        route = mock.route(method=verb, url=f"{_ORIGIN}{path}").respond(200, json=True)
        _run(lambda c: getattr(c, method_name)(123, "archived"))
    request = _only_call(route)
    assert request.method == verb
    assert request.url.path == path
    assert request.url.query == b""
    assert json.loads(request.content) == {"item": "archived", "object": "Ticket", "o_id": 123}
    assert request.headers["authorization"] == _AUTH
    assert request.headers["content-type"] == "application/json"


def test_list_articles_uses_by_ticket_endpoint() -> None:
    """list_articles issues GET /api/v1/ticket_articles/by_ticket/{id} and parses each item."""
    payload = [{"id": 1, "ticket_id": 123}, {"id": 2, "ticket_id": 123}]
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{_ORIGIN}/api/v1/ticket_articles/by_ticket/123").respond(
            200, json=payload
        )
        articles = _run(lambda c: c.list_articles(123))
    request = _only_call(route)
    assert [a.id for a in articles] == [1, 2]
    assert request.method == "GET"
    assert request.url.query == b""
    assert request.headers["authorization"] == _AUTH


def test_create_internal_article_posts_internal_html_without_retries() -> None:
    """create_internal_article posts an internal text/html article and never retries."""
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(f"{_ORIGIN}/api/v1/ticket_articles").respond(
            201, json={"id": 9, "ticket_id": 123}
        )
        article = _run(lambda c: c.create_internal_article(123, "Subject", "<p>Body</p>"))
    request = _only_call(route)
    assert article.id == 9
    assert request.method == "POST"
    assert json.loads(request.content) == {
        "ticket_id": 123,
        "subject": "Subject",
        "body": "<p>Body</p>",
        "content_type": "text/html",
        "internal": True,
    }
    assert request.headers["authorization"] == _AUTH


def test_article_creation_does_not_retry_a_server_error() -> None:
    """Article creation is non-idempotent, so a 503 is raised after exactly one attempt."""
    client, delays = _fast_client()

    async def scenario() -> None:
        async with client:
            await client.create_internal_article(1, "s", "b")

    with respx.mock() as mock:
        route = mock.post(f"{_ORIGIN}/api/v1/ticket_articles").respond(503)
        with pytest.raises(ServerError, match="after 1 attempts"):
            asyncio.run(scenario())
    assert route.call_count == 1
    assert delays == []


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, AuthError), (403, AuthError), (404, NotFoundError), (400, ClientError)],
)
def test_non_retryable_statuses_map_to_typed_errors_after_one_call(
    status: int, error: type[Exception]
) -> None:
    """Auth, not-found and other client errors are raised immediately without retries."""
    with respx.mock() as mock:
        route = mock.get(f"{_ORIGIN}/api/v1/tickets/1").respond(status)
        with pytest.raises(error, match=f"status={status}"):
            _run(lambda c: c.get_ticket(1))
    assert route.call_count == 1


@pytest.mark.parametrize(
    ("status", "error"), [(503, ServerError), (500, ServerError), (429, RateLimitError)]
)
def test_retryable_statuses_exhaust_default_attempts_then_raise(
    status: int, error: type[Exception]
) -> None:
    """Retryable statuses are attempted 1 + 3 times with exponential backoff, then raised."""
    client, delays = _fast_client()

    async def scenario() -> None:
        async with client:
            await client.get_ticket(1)

    with respx.mock() as mock:
        route = mock.get(f"{_ORIGIN}/api/v1/tickets/1").respond(status)
        with pytest.raises(error, match="after 4 attempts"):
            asyncio.run(scenario())
    assert route.call_count == 4
    assert delays == [0.2, 0.4, 0.8]


def test_retry_after_header_overrides_backoff_for_rate_limits() -> None:
    """A numeric Retry-After on a 429 is used as the delay before the next attempt."""
    client, delays = _fast_client()

    async def scenario() -> Any:
        async with client:
            return await client.get_ticket(1)

    with respx.mock() as mock:
        mock.get(f"{_ORIGIN}/api/v1/tickets/1").mock(
            side_effect=[
                httpx.Response(429, headers={"Retry-After": "2"}),
                httpx.Response(200, json={"id": 1, "number": "1", "title": "T"}),
            ]
        )
        ticket = asyncio.run(scenario())
    assert ticket.id == 1
    assert delays == [2.0]


def test_current_user_login_reads_users_me_once_per_client() -> None:
    """get_current_user_login issues GET /api/v1/users/me and caches the token's login."""

    async def twice(client: AsyncZammadClient) -> list[str | None]:
        return [await client.get_current_user_login(), await client.get_current_user_login()]

    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{_ORIGIN}/api/v1/users/me").respond(
            200, json={"id": 9, "login": "chronikwerk-api", "firstname": "Chronikwerk"}
        )
        logins = _run(twice)
    request = _only_call(route)
    assert logins == ["chronikwerk-api", "chronikwerk-api"]
    assert request.method == "GET"
    assert request.headers["authorization"] == _AUTH


def test_current_user_login_without_a_login_fails_closed() -> None:
    """A users/me body without a login is a ClientError, never an unknown service account."""
    with respx.mock() as mock:
        mock.get(f"{_ORIGIN}/api/v1/users/me").respond(200, json={"id": 9})
        with pytest.raises(ClientError, match="users/me response has no login"):
            _run(lambda c: c.get_current_user_login())
