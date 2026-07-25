"""Weekly period math for the delta leaderboard.

A period is one ISO week measured in Moscow time: it opens Monday 00:00 MSK and
closes the next Monday 00:00 MSK. MSK is a fixed UTC+3 offset (Russia has no DST),
so the conversion is a plain shift — no tz database needed.

Everything crossing the DB boundary is **naive UTC** (see utils/timeutils), so
`period_start_utc` returns naive UTC; only the human-facing bounds are rendered
in MSK.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from utils.timeutils import utcnow

# Russia dropped DST in 2014 — MSK is a permanent UTC+3.
MSK_OFFSET = timedelta(hours=3)


def _to_msk(dt_utc: datetime) -> datetime:
    """Naive UTC -> naive MSK."""
    return dt_utc + MSK_OFFSET


def _to_utc(dt_msk: datetime) -> datetime:
    """Naive MSK -> naive UTC."""
    return dt_msk - MSK_OFFSET


def current_period_key(now_utc: datetime | None = None) -> str:
    """ISO-week key for the period containing `now_utc`, e.g. ``2026-W30``.

    Uses ISO year (not calendar year) so the last days of December belong to the
    week that owns them — otherwise week 1 of the new year would collide with
    week 52/53 of the old one around New Year.
    """
    msk = _to_msk(now_utc if now_utc is not None else utcnow())
    iso_year, iso_week, _ = msk.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def period_start_utc(key: str) -> datetime:
    """Naive UTC instant at which the given period opened (Mon 00:00 MSK)."""
    iso_year, iso_week = _parse_key(key)
    # ISO weekday 1 == Monday.
    monday_msk = datetime.fromisocalendar(iso_year, iso_week, 1)
    return _to_utc(monday_msk)


def period_bounds_msk(key: str) -> tuple[datetime, datetime]:
    """(start, end) of the period in **MSK**, for display. `end` is the last
    moment that still belongs to the period (Sunday 23:59:59-ish), so rendering
    it as a date gives the inclusive "20–26 июля" range users expect."""
    iso_year, iso_week = _parse_key(key)
    start = datetime.fromisocalendar(iso_year, iso_week, 1)
    end = start + timedelta(days=7) - timedelta(seconds=1)
    return start, end


def previous_period_key(key: str) -> str:
    """The key of the period immediately before `key`."""
    start_utc = period_start_utc(key)
    # A second before this period opened is inside the previous one.
    return current_period_key(start_utc - timedelta(seconds=1))


def week_number(key: str) -> int:
    """The ISO week number alone (for the "неделя 30" header)."""
    return _parse_key(key)[1]


def _parse_key(key: str) -> tuple[int, int]:
    try:
        year_part, week_part = key.split("-W", 1)
        return int(year_part), int(week_part)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"malformed period key: {key!r}") from exc
