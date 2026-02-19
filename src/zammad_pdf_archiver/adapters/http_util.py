"""Shared HTTP client utilities (e.g. timeouts)."""

from __future__ import annotations

import httpx


def timeouts_for(seconds: float) -> httpx.Timeout:
    """Build httpx.Timeout with bounded connect/pool for fail-fast on unreachable upstreams."""
    total = float(seconds)
    connect = min(5.0, total)
    return httpx.Timeout(connect=connect, read=total, write=total, pool=connect)
