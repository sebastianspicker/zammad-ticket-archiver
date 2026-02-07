from __future__ import annotations

import time
from collections.abc import Callable


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

    def seen(self, key: str) -> bool:
        now = self._now()
        self._maybe_evict(now)
        expires_at = self._expires_at_by_key.get(key)
        if expires_at is None:
            return False
        if now >= expires_at:
            self._expires_at_by_key.pop(key, None)
            return False
        return True

    def add(self, key: str) -> None:
        now = self._now()
        self._maybe_evict(now)
        self._expires_at_by_key[key] = now + self._ttl_seconds

    def evict_expired(self) -> None:
        self._evict_expired_at(self._now())

    def _evict_expired_at(self, now: float) -> None:
        expired = [key for key, expires_at in self._expires_at_by_key.items() if now >= expires_at]
        for key in expired:
            self._expires_at_by_key.pop(key, None)
