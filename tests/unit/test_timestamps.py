"""Cover ISO 8601 date-duration arithmetic on aware datetimes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from chronikwerk.timestamps import add_iso8601_date_duration, parse_iso8601_date_duration


def _utc(year: int, month: int, day: int, hour: int = 12) -> datetime:
    """Build an aware UTC datetime at a fixed time of day."""
    return datetime(year, month, day, hour, 30, 15, tzinfo=UTC)


@pytest.mark.parametrize(
    ("start", "duration", "expected"),
    [
        (_utc(2026, 10, 30), "P30D", _utc(2026, 11, 29)),
        (_utc(2026, 10, 30), "P6Y", _utc(2032, 10, 30)),
        (_utc(2026, 10, 30), "P1Y6M", _utc(2028, 4, 30)),
        (_utc(2025, 1, 31), "P1M", _utc(2025, 2, 28)),
        (_utc(2024, 1, 31), "P1M", _utc(2024, 2, 29)),
        (_utc(2024, 2, 29), "P1Y", _utc(2025, 2, 28)),
        (_utc(2024, 2, 29), "P4Y", _utc(2028, 2, 29)),
        (_utc(2026, 11, 30), "P3M", _utc(2027, 2, 28)),
        (_utc(2026, 12, 31), "P1M", _utc(2027, 1, 31)),
        (_utc(2025, 1, 31), "P1M1D", _utc(2025, 3, 1)),
        (_utc(2026, 1, 1), "P12M", _utc(2027, 1, 1)),
    ],
)
def test_add_duration_clamps_to_month_end_then_adds_days(
    start: datetime, duration: str, expected: datetime
) -> None:
    assert add_iso8601_date_duration(start, duration) == expected


def test_add_duration_keeps_the_time_and_offset() -> None:
    start = datetime(2025, 1, 31, 23, 59, tzinfo=timezone(timedelta(hours=2)))

    result = add_iso8601_date_duration(start, "P1M")

    assert result == datetime(2025, 2, 28, 23, 59, tzinfo=timezone(timedelta(hours=2)))


def test_add_duration_requires_an_aware_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        add_iso8601_date_duration(datetime(2026, 1, 1), "P1D")


@pytest.mark.parametrize(
    "duration", ["", "P", "6Y", "p1y", "P-1Y", "P1.5Y", "PT1H", "P1W", "P1D1Y", "P0Y0M0D", "P1Y "]
)
def test_invalid_durations_are_rejected(duration: str) -> None:
    with pytest.raises(ValueError, match="ISO 8601"):
        parse_iso8601_date_duration(duration)


def test_result_beyond_year_9999_is_rejected() -> None:
    with pytest.raises(ValueError, match="out of range"):
        add_iso8601_date_duration(_utc(9000, 1, 1), "P1000Y")
    with pytest.raises(ValueError, match="out of range"):
        add_iso8601_date_duration(_utc(9999, 12, 31), "P1D")
