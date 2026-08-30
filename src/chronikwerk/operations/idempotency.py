"""Provide bounded TTL-based duplicate-delivery suppression."""

from __future__ import annotations

import time
from collections.abc import Callable

_DEFAULT_MAX_ENTRIES = 10_000


class InMemoryTTLSet:
    """In-memory idempotency set with expiring keys."""

    def __init__(
        self,
        *,
        ttl_seconds: float,
        now: Callable[[], float] = time.monotonic,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be >= 0")
        if max_entries <= 0:
            raise ValueError("max_entries must be > 0")
        self._ttl_seconds = float(ttl_seconds)
        self._now = now
        self._max_entries = max_entries
        self._expires_at_by_key: dict[str, float] = {}
        self._next_evict_at = float(self._now())

    def __len__(self) -> int:
        return len(self._expires_at_by_key)

    def _maybe_evict(self, now: float) -> None:
        if now < self._next_evict_at:
            return
        self._evict_expired_at(now)
        self._next_evict_at = now + min(60.0, max(1.0, self._ttl_seconds))

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

    def _add_sync(self, key: str) -> bool:
        now = self._now()
        self._maybe_evict(now)
        if key not in self._expires_at_by_key and len(self) >= self._max_entries:
            # Capacity decisions always consider every stale key, even when the
            # periodic eviction interval has not elapsed yet.
            self._evict_expired_at(now)
            if len(self) >= self._max_entries:
                return False
        self._expires_at_by_key[key] = now + self._ttl_seconds
        return True

    async def seen(self, key: str) -> bool:
        """Return whether a duplicate-delivery key is still within its TTL."""
        return self._seen_sync(key)

    async def add(self, key: str) -> bool:
        """Record a delivery key with expiration for later duplicate suppression."""
        return self._add_sync(key)

    async def try_claim(self, key: str) -> bool:
        """Claim a key once and report whether this caller won the race."""
        if self._seen_sync(key):
            return False
        return self._add_sync(key)

    def evict_expired(self) -> None:
        """Remove expired keys to bound the in-memory idempotency set."""
        self._evict_expired_at(self._now())

    def _evict_expired_at(self, now: float) -> None:
        for key, expires_at in list(self._expires_at_by_key.items()):
            if now >= expires_at:
                self._expires_at_by_key.pop(key, None)
