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
            json=["archived"],
            request=request,
        )

    monkeypatch.setattr(transport, "validate_url_policy_async", resolved_address)
    client = AsyncZammadClient(
        connection=_connection(),
        runtime=transport.ZammadRuntimeOptions(
            retry_policy=transport.RetryPolicy(max_retries=1, backoff_base_seconds=0.25),
            sleep=sleep,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler),
                headers={"Authorization": "Token token=test-token"},
            ),
            allow_private_networks=True,
        ),
    )

    tags = asyncio.run(client.list_tags(7))

    assert tags.root == ["archived"]
    assert len(attempts) == 2
    assert attempts[-1].url.path == "/api/v1/tags"
    assert attempts[-1].headers["authorization"] == "Token token=test-token"
    assert delays == [0.25]
    asyncio.run(client.aclose())


def test_client_maps_upstream_auth_failure_without_retrying(monkeypatch) -> None:
    async def resolved_address(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(transport, "validate_url_policy_async", resolved_address)
    client = AsyncZammadClient(
        connection=_connection(),
        runtime=transport.ZammadRuntimeOptions(
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(401, request=request))
            ),
            allow_private_networks=True,
        ),
    )

    with pytest.raises(AuthError, match="status=401"):
        asyncio.run(client.list_tags(9))
    asyncio.run(client.aclose())


@pytest.mark.parametrize("failure", [503, 429, "timeout", "connect"])
def test_exhausted_retries_preserve_bounded_attempts_and_error_class(monkeypatch, failure) -> None:
    from chronikwerk.zammad.errors import RateLimitError, ServerError

    attempts = []
    delays = []

    async def resolved_address(*_args, **_kwargs) -> None:
        return None

    async def sleep(seconds: float) -> None:
        delays.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic timeout", request=request)
        if failure == "connect":
            raise httpx.ConnectError("synthetic network failure", request=request)
        return httpx.Response(failure, headers={"Retry-After": "0.5"}, request=request)

    monkeypatch.setattr(transport, "validate_url_policy_async", resolved_address)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = AsyncZammadClient(
                connection=_connection(),
                runtime=transport.ZammadRuntimeOptions(
                    retry_policy=transport.RetryPolicy(max_retries=2),
                    sleep=sleep,
                    http_client=http,
                    allow_private_networks=True,
                ),
            )
            error_type = RateLimitError if failure == 429 else ServerError
            with pytest.raises(error_type, match="after 3 attempts"):
                await client.list_tags(7)
            await client.aclose()
            assert not http.is_closed
        assert len(attempts) == 3
        assert delays == ([0.5, 0.5] if failure == 429 else [0.2, 0.4])

    asyncio.run(scenario())
