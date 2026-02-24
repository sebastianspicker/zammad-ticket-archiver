import asyncio
from typing import Any

import structlog

from zammad_pdf_archiver.config.settings import Settings
from zammad_pdf_archiver.domain.idempotency import DeliveryIdStore, InMemoryTTLSet
from zammad_pdf_archiver.domain.redis_delivery_id import RedisDeliveryIdStore
from zammad_pdf_archiver.app.jobs.shutdown import is_shutting_down

log = structlog.get_logger(__name__)

_DELIVERY_ID_SETS: dict[int, InMemoryTTLSet] = {}
_REDIS_STORES: dict[tuple[str, int, str | None], RedisDeliveryIdStore] = {}
_STORE_GUARD = asyncio.Lock()

_IN_FLIGHT_TICKETS: set[int] = set()
_IN_FLIGHT_TICKETS_GUARD = asyncio.Lock()
_TICKET_LOCK_PREFIX = "zammad:ticket_lock:"
_TICKET_LOCK_TTL = 300  # 5 minutes fallback


def _get_redis_store(
    redis_url: str,
    ttl_seconds: int,
    prefix: str | None = None,
) -> RedisDeliveryIdStore:
    """Helper to deduplicate Redis store initialization."""
    cache_key = (redis_url, ttl_seconds, prefix)
    result = _REDIS_STORES.get(cache_key)
    if result is None:
        result = RedisDeliveryIdStore(redis_url, ttl_seconds, prefix=prefix or "")
        _REDIS_STORES[cache_key] = result
    return result


def _get_delivery_id_store(settings: Settings) -> DeliveryIdStore | None:
    """Delivery-ID store or None if idempotency off (ttl<=0). Caller must hold _STORE_GUARD."""
    ttl = int(settings.workflow.delivery_id_ttl_seconds)
    if ttl <= 0 or is_shutting_down():
        return None
    backend = (settings.workflow.idempotency_backend or "memory").strip().lower()
    if backend == "redis" and settings.workflow.redis_url:
        return _get_redis_store(settings.workflow.redis_url, ttl)
    
    result = _DELIVERY_ID_SETS.get(ttl)
    if result is None:
        result = InMemoryTTLSet(ttl_seconds=float(ttl))
        _DELIVERY_ID_SETS[ttl] = result
    return result


async def try_claim_delivery_id(settings: Settings, delivery_id: str) -> bool:
    """
    Atomically check and register delivery_id for idempotency.
    Returns True if this delivery was newly claimed (caller should proceed),
    False if already seen (caller should skip).
    """
    async with _STORE_GUARD:
        store = _get_delivery_id_store(settings)
        if store is None:
            return True
        return await store.try_claim(delivery_id)


def _get_ticket_lock_store(settings: Settings) -> RedisDeliveryIdStore | None:
    """Distributed ticket lock store or None if Redis off. Caller must hold _STORE_GUARD."""
    if is_shutting_down():
        return None
    backend = (settings.workflow.idempotency_backend or "memory").strip().lower()
    if backend == "redis" and settings.workflow.redis_url:
        return _get_redis_store(
            settings.workflow.redis_url, 
            _TICKET_LOCK_TTL, 
            prefix=_TICKET_LOCK_PREFIX
        )
    return None


async def try_acquire_ticket(settings: Settings, ticket_id: int) -> bool:
    # 1. Local process lock (prevent intra-process races)
    async with _IN_FLIGHT_TICKETS_GUARD:
        if ticket_id in _IN_FLIGHT_TICKETS:
            return False
        _IN_FLIGHT_TICKETS.add(ticket_id)

    # 2. Distributed lock (if enabled)
    async with _STORE_GUARD:
        store = _get_ticket_lock_store(settings)
        if store is not None:
            try:
                claimed = await store.try_claim(str(ticket_id))
                if not claimed:
                    # Release local lock if distributed lock failed
                    async with _IN_FLIGHT_TICKETS_GUARD:
                        _IN_FLIGHT_TICKETS.discard(ticket_id)
                    return False
            except Exception:
                # If Redis fails, we fall back to local lock only (warn if needed)
                log.warning("process_ticket.redis_lock_failed_fallback_to_local", ticket_id=ticket_id)

    return True


async def release_ticket(settings: Settings, ticket_id: int) -> None:
    # 1. Distributed lock
    async with _STORE_GUARD:
        store = _get_ticket_lock_store(settings)
        if store is not None:
            try:
                await store.release(str(ticket_id))
            except Exception:
                log.warning("process_ticket.redis_unlock_failed", ticket_id=ticket_id)

    # 2. Local process lock
    async with _IN_FLIGHT_TICKETS_GUARD:
        _IN_FLIGHT_TICKETS.discard(ticket_id)
