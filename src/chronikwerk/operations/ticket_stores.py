"""Manage process-local idempotency and per-ticket exclusion stores."""

from __future__ import annotations

import asyncio

from chronikwerk.operations.idempotency import InMemoryTTLSet
from chronikwerk.operations.shutdown import is_shutting_down

_DELIVERY_ID_SETS: dict[int, InMemoryTTLSet] = {}
_STORE_GUARD = asyncio.Lock()
_IN_FLIGHT_TICKETS: set[int] = set()
_IN_FLIGHT_TICKETS_GUARD = asyncio.Lock()


def _get_delivery_id_store(ttl_seconds: int) -> InMemoryTTLSet | None:
    ttl = int(ttl_seconds)
    if ttl <= 0 or is_shutting_down():
        return None
    store = _DELIVERY_ID_SETS.get(ttl)
    if store is None:
        store = InMemoryTTLSet(ttl_seconds=float(ttl))
        _DELIVERY_ID_SETS[ttl] = store
    return store


async def try_claim_delivery_id(ttl_seconds: int, delivery_id: str) -> bool:
    """Claim a webhook delivery once to suppress duplicate processing."""
    async with _STORE_GUARD:
        store = _get_delivery_id_store(ttl_seconds)
        if store is None:
            return True
        return await store.try_claim(delivery_id)


async def try_acquire_ticket(ticket_id: int) -> bool:
    """Acquire per-ticket exclusion before concurrent archival begins."""
    async with _IN_FLIGHT_TICKETS_GUARD:
        if ticket_id in _IN_FLIGHT_TICKETS:
            return False
        _IN_FLIGHT_TICKETS.add(ticket_id)
        return True


async def release_ticket(ticket_id: int) -> None:
    """Release a ticket claim after its pipeline completes or aborts."""
    async with _IN_FLIGHT_TICKETS_GUARD:
        _IN_FLIGHT_TICKETS.discard(ticket_id)


async def aclose_stores() -> None:
    """Close all process-local stores during application shutdown."""
    async with _STORE_GUARD:
        _DELIVERY_ID_SETS.clear()
    async with _IN_FLIGHT_TICKETS_GUARD:
        _IN_FLIGHT_TICKETS.clear()


def reset_for_tests() -> None:
    """Clear process-local diagnostic state between isolated tests."""
    _DELIVERY_ID_SETS.clear()
    _IN_FLIGHT_TICKETS.clear()
