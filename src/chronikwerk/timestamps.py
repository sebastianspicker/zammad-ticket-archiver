"""Provide shared UTC timestamp values and display formatting helpers."""

from __future__ import annotations

import calendar
import re
from datetime import MAXYEAR, UTC, datetime, timedelta

_DATE_DURATION = re.compile(r"^P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?$")


def now_utc() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(UTC)


def format_timestamp_utc(dt: datetime) -> str:
    """Format a UTC timestamp for deterministic archive filenames."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    dt_utc = dt.astimezone(UTC)
    return dt_utc.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def format_time(value: datetime) -> str:
    """Format an aware time as the journal and catalog store and compare it (UTC, microseconds)."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_iso8601_date_duration(duration: str) -> tuple[int, int, int]:
    """Parse a positive ISO 8601 duration with only Y, M and D parts into (years, months, days)."""
    match = _DATE_DURATION.fullmatch(duration)
    if match is None or duration == "P":
        raise ValueError(f"invalid ISO 8601 date duration {duration!r}; use e.g. P6Y, P1Y6M, P30D")
    years, months, days = (int(part) if part else 0 for part in match.groups())
    if years == months == days == 0:
        raise ValueError(f"ISO 8601 date duration {duration!r} must be positive")
    return years, months, days


def add_iso8601_date_duration(start: datetime, duration: str) -> datetime:
    """Add a Y/M/D duration to an aware datetime, clamping the day to the month's end."""
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be a timezone-aware datetime")
    years, months, days = parse_iso8601_date_duration(duration)
    year, month_zero = divmod(start.year * 12 + (start.month - 1) + years * 12 + months, 12)
    if not 1 <= year <= MAXYEAR:
        raise ValueError(f"result of adding {duration!r} to {start.isoformat()} is out of range")
    day = min(start.day, calendar.monthrange(year, month_zero + 1)[1])
    try:
        return start.replace(year=year, month=month_zero + 1, day=day) + timedelta(days=days)
    except OverflowError as exc:
        raise ValueError(
            f"result of adding {duration!r} to {start.isoformat()} is out of range"
        ) from exc
