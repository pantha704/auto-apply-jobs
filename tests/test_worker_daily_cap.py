from datetime import datetime, timezone

from jobhunt_time import ist_day_bounds


def test_daily_cap_uses_ist_calendar_day_boundaries():
    now = datetime(2026, 8, 19, 18, 45, tzinfo=timezone.utc)  # 00:15 IST on Aug 20
    start, end = ist_day_bounds(now)
    assert start == "2026-08-19T18:30:00+00:00"
    assert end == "2026-08-20T18:30:00+00:00"
