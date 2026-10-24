"""Pin the mapping from Zammad's documented ticket, tag and article responses."""

from __future__ import annotations

import asyncio
from typing import Any

import respx
from pydantic import SecretStr

from chronikwerk.configuration.zammad import ZammadConnection
from chronikwerk.zammad.dto import Ticket
from chronikwerk.zammad.gateway import AsyncZammadClient
from tests.support.zammad_fixtures import load_zammad_fixture

_ORIGIN = "https://zammad.example"


def _ticket_body(**overrides: Any) -> dict[str, Any]:
    """Return the documented expanded ticket body with optional key overrides."""
    return {**load_zammad_fixture("ticket_show_expand.json"), **overrides}


def _get_ticket(body: dict[str, Any]) -> tuple[Ticket, respx.Route]:
    """Fetch ticket 42 through the real gateway against a mocked Zammad."""
    connection = ZammadConnection(
        origin=_ORIGIN, api_token=SecretStr("token-value"), allow_private_origin=True
    )

    async def scenario() -> Ticket:
        async with AsyncZammadClient(connection=connection) as client:
            return await client.get_ticket(42)

    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(f"{_ORIGIN}/api/v1/tickets/42").respond(200, json=body)
        return asyncio.run(scenario()), route


def test_get_ticket_requests_expanded_relations() -> None:
    _ticket, route = _get_ticket(_ticket_body())

    assert dict(route.calls.last.request.url.params) == {"expand": "true"}


def test_expanded_user_logins_map_to_owner_updated_by_and_customer() -> None:
    ticket, _route = _get_ticket(_ticket_body())

    assert ticket.owner is not None and ticket.owner.login == "alice"
    assert ticket.updated_by is not None and ticket.updated_by.login == "bob"
    assert ticket.customer is not None
    assert (ticket.customer.id, ticket.customer.login) == (7, "nicole.braun@zammad.org")


def test_custom_object_attributes_are_read_from_top_level_keys() -> None:
    ticket, _route = _get_ticket(_ticket_body())

    assert ticket.attribute("archive_path") == "Customers > ACME"
    assert ticket.attribute("archive_user_mode") == "owner"
    assert ticket.attribute("archive_user") is None
    assert ticket.attribute("not_a_field") is None


def test_unassigned_owner_placeholder_maps_to_no_owner() -> None:
    # Zammad reports an unassigned ticket as owner_id 1 with the system login "-".
    ticket, _route = _get_ticket(_ticket_body(owner_id=1, owner="-"))

    assert ticket.owner is None


def test_unexpanded_response_still_validates_without_relations() -> None:
    body = {
        key: value
        for key, value in _ticket_body().items()
        if key not in {"owner", "updated_by", "customer", "created_by"}
    }

    ticket, _route = _get_ticket(body)

    assert (ticket.owner, ticket.updated_by, ticket.customer) == (None, None, None)


def _fetch(path: str, body: Any, call: Any) -> Any:
    """Run one gateway call against a mocked documented response body."""
    connection = ZammadConnection(
        origin=_ORIGIN, api_token=SecretStr("token-value"), allow_private_origin=True
    )

    async def scenario() -> Any:
        async with AsyncZammadClient(connection=connection) as client:
            return await call(client)

    with respx.mock(assert_all_called=True) as mock:
        mock.get(f"{_ORIGIN}{path}").respond(200, json=body)
        return asyncio.run(scenario())


def test_documented_tags_response_parses() -> None:
    tags = _fetch("/api/v1/tags", load_zammad_fixture("tags.json"), lambda c: c.list_tags(42))

    assert tags.root == ["pdf:sign"]


def test_documented_articles_response_parses() -> None:
    articles = _fetch(
        "/api/v1/ticket_articles/by_ticket/42",
        load_zammad_fixture("ticket_articles_by_ticket.json"),
        lambda c: c.list_articles(42),
    )

    assert [article.id for article in articles] == [101, 102]
    assert articles[0].from_ == "Nicole Braun <nicole.braun@zammad.org>"
    assert articles[0].attachments is not None
    assert articles[0].attachments[0].size == 29935
    assert articles[1].internal is True
