"""Shared HTTP client utilities (e.g. timeouts)."""

from __future__ import annotations

import httpx
from starlette.types import Receive


def timeouts_for(seconds: float) -> httpx.Timeout:
    """Build httpx.Timeout with bounded connect/pool for fail-fast on unreachable upstreams."""
    total = float(seconds)
    connect = min(5.0, total)
    return httpx.Timeout(connect=connect, read=total, write=total, pool=connect)


async def drain_stream(receive: Receive) -> None:
    """
    Consume the request body so the connection is left in a clean state.
    Shared helper to replace duplicates in middleware (Bug #P3-3).
    """
    while True:
        message = await receive()
        if message.get("type") == "http.disconnect":
            return
        if message.get("type") == "http.request" and not message.get("more_body", False):
            return
