from datetime import datetime, timedelta, timezone
from typing import Literal

RefreshMode = Literal["full", "stats_only", "background_full"]

STALE_THRESHOLD = timedelta(hours=1)
BACKGROUND_THRESHOLD = timedelta(hours=2)

STATS_SWEEP_THRESHOLD = timedelta(minutes=5)

TOP_PLAYS_STALE_THRESHOLD = timedelta(minutes=3)

def is_stale(last_api_update, threshold: timedelta = STALE_THRESHOLD) -> bool:
    if last_api_update is None:
        return True
    ts = last_api_update
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts) > threshold

def needs_blocking_refresh(last_api_update) -> bool:
    return is_stale(last_api_update, STALE_THRESHOLD)

def needs_background_refresh(last_full_update) -> bool:
    return is_stale(last_full_update, BACKGROUND_THRESHOLD)

def needs_stats_sweep(last_api_update) -> bool:
    return is_stale(last_api_update, STATS_SWEEP_THRESHOLD)

def needs_top_plays_refresh(last_api_update) -> bool:
    return is_stale(last_api_update, TOP_PLAYS_STALE_THRESHOLD)
