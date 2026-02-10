from __future__ import annotations

import asyncio

from zammad_pdf_archiver.domain.idempotency import InMemoryTTLSet


async def _run_ttl_expiry() -> None:
    now_value = 1000.0

    def now() -> float:
        return now_value

    ttl = InMemoryTTLSet(ttl_seconds=5.0, now=now)

    assert await ttl.seen("abc") is False
    await ttl.add("abc")
    assert await ttl.seen("abc") is True

    now_value = 1004.999
    assert await ttl.seen("abc") is True

    now_value = 1005.0
    assert await ttl.seen("abc") is False

    await ttl.add("abc")
    assert await ttl.seen("abc") is True


def test_ttl_expiry() -> None:
    asyncio.run(_run_ttl_expiry())


async def _run_eviction() -> None:
    now_value = 0.0

    def now() -> float:
        return now_value

    ttl = InMemoryTTLSet(ttl_seconds=1.0, now=now)

    for idx in range(200):
        await ttl.add(f"k{idx}")

    assert len(ttl) == 200

    now_value = 2.0
    await ttl.add("fresh")

    assert len(ttl) == 1


def test_add_triggers_eviction_of_expired_keys() -> None:
    asyncio.run(_run_eviction())
