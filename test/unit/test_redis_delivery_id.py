"""Tests for Redis delivery-ID store (optional durable idempotency)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from zammad_pdf_archiver.domain.redis_delivery_id import RedisDeliveryIdStore


async def _run_redis_store_seen_add() -> None:
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(side_effect=[None, "1", "1"])
    mock_redis.set = AsyncMock(return_value=True)

    store = RedisDeliveryIdStore("redis://localhost/0", 3600)
    with patch.object(store, "_client", return_value=mock_redis):
        assert await store.seen("id1") is False
        await store.add("id1")
        assert await store.seen("id1") is True
        assert await store.seen("id1") is True

    assert mock_redis.set.await_count == 1
    mock_redis.set.assert_called_once_with("zammad:delivery_id:id1", "1", ex=3600)


def test_redis_store_seen_and_add() -> None:
    asyncio.run(_run_redis_store_seen_add())
