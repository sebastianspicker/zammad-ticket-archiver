"""Provide shared UTC timestamp values and display formatting helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def now_utc() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def format_timestamp_utc(dt: datetime) -> str:
    """Format a UTC timestamp for deterministic archive filenames."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt_utc = dt.astimezone(UTC)
    return dt_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")
