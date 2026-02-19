from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol


class DeliveryIdStore(Protocol):
    """Protocol for delivery-ID idempotency (in-memory or durable e.g. Redis)."""

    async def seen(self, key: str) -> bool:
        """Return True if key was already seen and is still within TTL."""
        ...

    async def add(self, key: str) -> None:
        """Record key as seen (idempotent for same key within TTL)."""
        ...

    async def try_claim(self, key: str) -> bool:
        """Atomically claim key if not yet seen. Return True if claimed, False if already seen (Bug #17)."""
        ...


class InMemoryTTLSet:
    def __init__(self, *, ttl_seconds: float, now: Callable[[], float] = time.monotonic) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be >= 0")
        self._ttl_seconds = float(ttl_seconds)
        self._now = now
        self._expires_at_by_key: dict[str, float] = {}
        self._next_evict_at = float(self._now())

    def __len__(self) -> int:
        return len(self._expires_at_by_key)

    def _maybe_evict(self, now: float) -> None:
        # Best-effort: periodically purge expired keys so the set doesn't grow forever
        # when keys are mostly unique.
        if now < self._next_evict_at:
            return
        self._evict_expired_at(now)
        interval = min(60.0, max(1.0, self._ttl_seconds))
        self._next_evict_at = now + interval

    def _seen_sync(self, key: str) -> bool:
        now = self._now()
        self._maybe_evict(now)
        expires_at = self._expires_at_by_key.get(key)
        if expires_at is None:
            return False
        if now >= expires_at:
            self._expires_at_by_key.pop(key, None)
            return False
        return True

    def _add_sync(self, key: str) -> None:
        now = self._now()
        self._maybe_evict(now)
        self._expires_at_by_key[key] = now + self._ttl_seconds

    async def seen(self, key: str) -> bool:
        return self._seen_sync(key)

    async def add(self, key: str) -> None:
        self._add_sync(key)

    async def try_claim(self, key: str) -> bool:
        if self._seen_sync(key):
            return False
        self._add_sync(key)
        return True

    def evict_expired(self) -> None:
        self._evict_expired_at(self._now())

    def _evict_expired_at(self, now: float) -> None:
        # Directly remove expired keys during iteration using list() to avoid
        # "dictionary changed size during iteration" error.
        expired_keys = [
            key for key, expires_at in self._expires_at_by_key.items() if now >= expires_at
        ]
        for key in expired_keys:
            self._expires_at_by_key.pop(key, None)
