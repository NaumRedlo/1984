from __future__ import annotations

from datetime import datetime, timedelta

from utils.timeutils import utcnow

MSK_OFFSET = timedelta(hours=3)

def _to_msk(dt_utc: datetime) -> datetime:
    return dt_utc + MSK_OFFSET

def _to_utc(dt_msk: datetime) -> datetime:
    return dt_msk - MSK_OFFSET

def current_period_key(now_utc: datetime | None = None) -> str:
    msk = _to_msk(now_utc if now_utc is not None else utcnow())
    iso_year, iso_week, _ = msk.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"

def period_start_utc(key: str) -> datetime:
    iso_year, iso_week = _parse_key(key)

    monday_msk = datetime.fromisocalendar(iso_year, iso_week, 1)
    return _to_utc(monday_msk)

def period_bounds_msk(key: str) -> tuple[datetime, datetime]:
    iso_year, iso_week = _parse_key(key)
    start = datetime.fromisocalendar(iso_year, iso_week, 1)
    end = start + timedelta(days=7) - timedelta(seconds=1)
    return start, end

def previous_period_key(key: str) -> str:
    start_utc = period_start_utc(key)

    return current_period_key(start_utc - timedelta(seconds=1))

def week_number(key: str) -> int:
    return _parse_key(key)[1]

def _parse_key(key: str) -> tuple[int, int]:
    try:
        year_part, week_part = key.split("-W", 1)
        return int(year_part), int(week_part)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"malformed period key: {key!r}") from exc
