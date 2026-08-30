"""Verify Zammad request retries and status mapping without a network service."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import SecretStr

from chronikwerk.configuration.zammad import ZammadConnection
from chronikwerk.zammad import transport
from chronikwerk.zammad.errors import AuthError
from chronikwerk.zammad.gateway import AsyncZammadClient


def _connection() -> ZammadConnection:
    """Build the fixed safe connection used by local transport tests."""
    return ZammadConnection(
        origin="https://zammad.example.test",
        api_token=SecretStr("test-token"),
        allow_private_origin=True,
    )


def test_client_retries_a_server_error_then_preserves_request_identity(monkeypatch) -> None:
    attempts: list[httpx.Request] = []
    delays: list[float] = []

    async def resolved_address(*_args, **_kwargs) -> None:
        return None

    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            json={"id": 7, "number": "7", "title": "Archived ticket"},
            request=request,
        )

    monkeypatch.setattr(transport, "validate_url_policy_async", resolved_address)
    client = AsyncZammadClient(
        connection=_connection(),
        _runtime=transport._ZammadRuntimeOptions(
            retry_policy=transport._RetryPolicy(max_retries=1, backoff_base_seconds=0.25),
            sleep=sleep,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                headers={"Authorization": "Token token=test-token"},
            ),
            allow_private_networks=True,
        ),
    )

    ticket = asyncio.run(client.get_ticket(7))

    assert ticket.id == 7
    assert len(attempts) == 2
    assert attempts[-1].url.path == "/api/v1/tickets/7"
    assert attempts[-1].headers["authorization"] == "Token token=test-token"
    assert delays == [0.25]
    asyncio.run(client.aclose())


def test_client_maps_upstream_auth_failure_without_retrying(monkeypatch) -> None:
    async def resolved_address(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(transport, "validate_url_policy_async", resolved_address)
    client = AsyncZammadClient(
        connection=_connection(),
        _runtime=transport._ZammadRuntimeOptions(
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(401, request=request))
            ),
            allow_private_networks=True,
        ),
    )

    with pytest.raises(AuthError, match="status=401"):
        asyncio.run(client.get_ticket(9))
    asyncio.run(client.aclose())
