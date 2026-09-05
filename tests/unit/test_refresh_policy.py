from datetime import timedelta

from services.refresh import needs_blocking_refresh, needs_top_plays_refresh
from services.refresh.policy import TOP_PLAYS_STALE_THRESHOLD
from utils.timeutils import utcnow

def test_none_is_always_stale():
    assert needs_top_plays_refresh(None) is True

def test_fresh_timestamp_does_not_need_refresh():
    ts = utcnow() - timedelta(seconds=30)
    assert needs_top_plays_refresh(ts) is False

def test_timestamp_past_the_tight_window_needs_refresh():
    ts = utcnow() - (TOP_PLAYS_STALE_THRESHOLD + timedelta(seconds=1))
    assert needs_top_plays_refresh(ts) is True

def test_tighter_than_the_general_blocking_threshold():

    ts = utcnow() - timedelta(minutes=10)
    assert needs_top_plays_refresh(ts) is True
    assert needs_blocking_refresh(ts) is False
