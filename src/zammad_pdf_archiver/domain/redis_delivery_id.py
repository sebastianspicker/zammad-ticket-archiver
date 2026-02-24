"""Redis-backed delivery-ID store for durable idempotency (PRD §8.2).
Requires optional dependency: pip install zammad-pdf-archiver[redis]."""

from __future__ import annotations

_REDIS_PREFIX = "zammad:delivery_id:"


class RedisDeliveryIdStore:
    """DeliveryIdStore implementation using Redis with TTL."""

    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int,
        prefix: str = "zammad:delivery_id:",
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0 for Redis store")
        self._redis_url = redis_url
        self._ttl_seconds = ttl_seconds
        self._prefix = prefix
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
            self._redis = Redis.from_url(
                self._redis_url,
                decode_responses=True,
                socket_timeout=5.0,
                socket_connect_timeout=5.0,
            )
        return self._redis

    def _key(self, key: str) -> str:
        return self._prefix + key

    async def seen(self, key: str) -> bool:
        redis = self._client()
        value = await redis.get(self._key(key))
        return value is not None

    async def add(self, key: str) -> None:
        redis = self._client()
        await redis.set(self._key(key), "1", ex=self._ttl_seconds)

    async def try_claim(self, key: str) -> bool:
        """Atomically claim key (SET NX EX). True if claimed, False if seen (Bug #17)."""
        redis = self._client()
        full_key = self._key(key)
        # SET key "1" NX EX ttl: set only if not exists, with TTL; return True if key was set.
        return bool(await redis.set(full_key, "1", ex=self._ttl_seconds, nx=True))

    async def release(self, key: str) -> None:
        """Release a claimed key (delete it)."""
        redis = self._client()
        await redis.delete(self._key(key))

    async def aclose(self) -> None:
        """Close the Redis connection if it was opened."""
        if self._redis is not None:
            # redis.asyncio.Redis has an aclose method (alias for close in some versions).
            await self._redis.aclose()  # type: ignore
            self._redis = None
