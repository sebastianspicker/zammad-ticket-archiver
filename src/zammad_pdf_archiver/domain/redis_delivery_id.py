"""Redis-backed delivery-ID store for durable idempotency (PRD §8.2).
Requires optional dependency: pip install zammad-pdf-archiver[redis]."""

from __future__ import annotations

_REDIS_PREFIX = "zammad:delivery_id:"


class RedisDeliveryIdStore:
    """DeliveryIdStore implementation using Redis with TTL."""

    def __init__(self, redis_url: str, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0 for Redis store")
        self._redis_url = redis_url
        self._ttl_seconds = ttl_seconds
        self._redis: object | None = None

    def _client(self):  # noqa: ANN201
        try:
            from redis.asyncio import Redis  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "Redis backend requires the redis package. "
                "Install with: pip install zammad-pdf-archiver[redis]"
            ) from e
        if self._redis is None:
            self._redis = Redis.from_url(self._redis_url, decode_responses=True)
        return self._redis

    def _key(self, key: str) -> str:
        return _REDIS_PREFIX + key

    async def seen(self, key: str) -> bool:
        redis = self._client()
        value = await redis.get(self._key(key))
        return value is not None

    async def add(self, key: str) -> None:
        redis = self._client()
        await redis.set(self._key(key), "1", ex=self._ttl_seconds)
