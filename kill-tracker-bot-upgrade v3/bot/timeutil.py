"""Week window helpers: Sunday 08:00 -> next Sunday 08:00 in the configured time zone."""
from __future__ import annotations

from datetime import datetime, time, timedelta, tzinfo


def _sunday_start(local_now: datetime, tz: tzinfo, hour: int) -> datetime:
    # Monday=0 ... Sunday=6  -> days since the latest Sunday
    days_since_sunday = (local_now.weekday() + 1) % 7
    start_date = local_now.date() - timedelta(days=days_since_sunday)
    start = datetime.combine(start_date, time(hour, 0), tzinfo=tz)
    if start > local_now:  # it is Sunday but before the start hour
        start = datetime.combine(start_date - timedelta(days=7), time(hour, 0), tzinfo=tz)
    return start


def week_window(now: datetime, tz: tzinfo, hour: int = 8, weeks_back: int = 0) -> tuple[datetime, datetime]:
    """Return (start, end) of the Sunday-to-Sunday window that contains `now`.

    weeks_back=1 gives the previous complete window. `now` must be timezone-aware.
    """
    local_now = now.astimezone(tz)
    start = _sunday_start(local_now, tz, hour)
    start_date = start.date() - timedelta(days=7 * weeks_back)
    start = datetime.combine(start_date, time(hour, 0), tzinfo=tz)
    end = datetime.combine(start_date + timedelta(days=7), time(hour, 0), tzinfo=tz)
    return start, end


def fmt_window(start: datetime, end: datetime) -> str:
    return f"{start:%a %d %b %Y, %I:%M %p} → {end:%a %d %b %Y, %I:%M %p}"
