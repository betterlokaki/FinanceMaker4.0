"""Trading-day time window helpers for conclusion reports."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")


def local_day_bounds_utc(trading_day: date) -> tuple[datetime, datetime]:
    """Return UTC bounds for a New York calendar trading day."""
    start = datetime.combine(trading_day, time.min, tzinfo=NY_TZ)
    end = datetime.combine(trading_day, time.max, tzinfo=NY_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def local_date_range_bounds_utc(
    start_date: date,
    end_date: date,
) -> tuple[datetime, datetime]:
    """Return UTC bounds for an inclusive New York calendar date range."""
    start, _ = local_day_bounds_utc(start_date)
    _, end = local_day_bounds_utc(end_date)
    return start, end


def session_bounds_utc(trading_day: date) -> tuple[datetime, datetime]:
    """Return UTC bounds for the 4:00-20:00 New York trading session."""
    start = datetime.combine(trading_day, time(4, 0), tzinfo=NY_TZ)
    end = datetime.combine(trading_day, time(20, 0), tzinfo=NY_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def first_rth_five_minutes_utc(trading_day: date) -> tuple[datetime, datetime]:
    """Return UTC bounds for the 9:30-9:35 New York entry window."""
    start = datetime.combine(trading_day, time(9, 30), tzinfo=NY_TZ)
    end = datetime.combine(trading_day, time(9, 35), tzinfo=NY_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
