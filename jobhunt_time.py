"""Dependency-free time helpers shared by job-hunt workers."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def ist_day_bounds(now: datetime | None = None) -> tuple[str, str]:
    """Return the current IST calendar day's [start, end) as UTC ISO timestamps."""
    local_now = now.astimezone(IST) if now is not None else datetime.now(IST)
    start_local = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        start_local.astimezone(UTC).isoformat(),
        (start_local + timedelta(days=1)).astimezone(UTC).isoformat(),
    )
