"""Weekly period maths for the delta leaderboard (services/leaderboard/periods.py).

The period boundary is Monday 00:00 MSK, i.e. Sunday 21:00 UTC — the DB stores
naive UTC, so getting this shift wrong would silently mis-assign every snapshot.
"""

from datetime import datetime, timedelta

from services.leaderboard.periods import (
    current_period_key, period_start_utc, period_bounds_msk,
    previous_period_key, week_number,
)


def test_boundary_flips_at_monday_midnight_msk():
    # 2026-07-19 21:00 UTC == Mon 2026-07-20 00:00 MSK
    before = datetime(2026, 7, 19, 20, 59, 59)
    after = datetime(2026, 7, 19, 21, 0, 0)
    assert current_period_key(before) == "2026-W29"
    assert current_period_key(after) == "2026-W30"


def test_period_start_is_naive_utc():
    assert period_start_utc("2026-W30") == datetime(2026, 7, 19, 21, 0, 0)


def test_bounds_are_monday_to_sunday_in_msk():
    start, end = period_bounds_msk("2026-W30")
    assert (start.day, start.month) == (20, 7)
    assert (end.day, end.month) == (26, 7)     # the "20–26 июля" header range
    assert start.weekday() == 0 and end.weekday() == 6


def test_previous_period():
    assert previous_period_key("2026-W30") == "2026-W29"
    # ISO year rolls back correctly, not to "2026-W00"
    assert previous_period_key("2026-W01") == "2025-W52"


def test_key_round_trips_for_every_week():
    for w in range(1, 53):
        key = f"2026-W{w:02d}"
        assert current_period_key(period_start_utc(key)) == key
        assert week_number(key) == w


def test_new_year_uses_iso_year_not_calendar_year():
    # Dec 30 2025 already belongs to ISO week 1 of 2026 — a calendar-year key
    # would collide with week 1 of 2025.
    assert current_period_key(datetime(2025, 12, 30, 12, 0)) == "2026-W01"


def test_every_instant_in_a_week_maps_to_the_same_key():
    start = period_start_utc("2026-W30")
    for hours in (0, 1, 24, 72, 167):
        assert current_period_key(start + timedelta(hours=hours)) == "2026-W30"
    assert current_period_key(start + timedelta(hours=168)) == "2026-W31"
