"""Two-speed background refresh (services/refresh/policy.py).

The leaderboard ranks live osu! stats, so a cheap stats-only sweep runs every
few minutes. The expensive pass (best scores + titles) must keep its slow
cadence — and crucially must be scheduled off its OWN timestamp: every refresh,
including the frequent sweep, bumps last_api_update, so gating the full pass on
that would starve it forever.
"""

from datetime import timedelta

from services.refresh import (
    BACKGROUND_THRESHOLD, STATS_SWEEP_THRESHOLD,
    needs_background_refresh, needs_stats_sweep,
)
from utils.timeutils import utcnow


def _ago(**kw):
    return utcnow() - timedelta(**kw)


def test_sweep_is_the_faster_cadence():
    assert STATS_SWEEP_THRESHOLD < BACKGROUND_THRESHOLD


def test_stats_sweep_picks_up_users_after_a_few_minutes():
    assert needs_stats_sweep(_ago(minutes=6)) is True
    assert needs_stats_sweep(_ago(minutes=1)) is False
    # Never synced at all -> definitely due.
    assert needs_stats_sweep(None) is True


def test_full_pass_keeps_its_slow_cadence():
    assert needs_background_refresh(_ago(hours=3)) is True
    assert needs_background_refresh(_ago(minutes=30)) is False
    assert needs_background_refresh(None) is True


def test_full_pass_is_not_starved_by_the_frequent_sweep():
    """The regression this split exists to prevent.

    A user swept a minute ago (last_api_update fresh) but last fully refreshed
    hours ago must still be due for the full pass.
    """
    just_swept = _ago(minutes=1)
    long_since_full = _ago(hours=5)

    assert needs_stats_sweep(just_swept) is False        # no need to re-sweep
    assert needs_background_refresh(long_since_full) is True  # but a full pass is owed
