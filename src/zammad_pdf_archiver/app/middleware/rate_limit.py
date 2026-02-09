from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import monotonic

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from zammad_pdf_archiver.config.settings import Settings

_INGEST_PATH = "/ingest"
_METRICS_PATH = "/metrics"


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


class _InMemoryTokenBucketLimiter:
    def __init__(
        self,
        *,
        rps: float,
        burst: int,
        max_entries: int = 10_000,
        now=monotonic,
    ) -> None:
        self._rps = float(rps)
        self._burst = float(burst)
        self._max_entries = int(max_entries)
        self._now = now
        self._lock = asyncio.Lock()
        self._buckets: dict[str, _Bucket] = {}

    async def allow(self, key: str) -> bool:
        now = float(self._now())
        async with self._lock:
            if len(self._buckets) > self._max_entries:
                # Evict oldest buckets (by updated_at). Cap eviction per call to avoid
                # holding the lock too long under heavy load (P3 latency).
                sorted_buckets = sorted(
                    self._buckets.items(), key=lambda item: item[1].updated_at
                )
                excess_count = len(self._buckets) - self._max_entries + 1
                max_evict_per_call = 2000
                to_evict = min(excess_count, max_evict_per_call)
                for old_key, _ in sorted_buckets[:to_evict]:
                    self._buckets.pop(old_key, None)

            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=self._burst, updated_at=now)
                self._buckets[key] = bucket

            elapsed = max(0.0, now - bucket.updated_at)
            if self._rps > 0:
                bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._rps)
            bucket.updated_at = now

            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True

            return False


def _client_key(scope: Scope) -> str:
    client = scope.get("client")
    if isinstance(client, (list, tuple)) and client:
        host = client[0]
        if isinstance(host, str) and host:
            return host
    return "unknown"


def _rate_limited() -> JSONResponse:
    return JSONResponse(status_code=429, content={"detail": "rate_limited"})


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp, *, settings: Settings | None) -> None:
        self.app = app

        if settings is None:
            self._enabled = False
            self._paths: frozenset[str] = frozenset()
            self._limiter: _InMemoryTokenBucketLimiter | None = None
            return

        config = settings.hardening.rate_limit
        self._enabled = bool(config.enabled)
        self._paths = frozenset(
            {_INGEST_PATH, _METRICS_PATH} if config.include_metrics else {_INGEST_PATH}
        )
        self._limiter = _InMemoryTokenBucketLimiter(rps=config.rps, burst=config.burst)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not self._enabled or scope.get("path") not in self._paths:
            await self.app(scope, receive, send)
            return

        limiter = self._limiter
        if limiter is None:
            await self.app(scope, receive, send)
            return

        key = _client_key(scope)
        if not await limiter.allow(key):
            await _rate_limited()(scope, receive, send)
            return

        await self.app(scope, receive, send)

