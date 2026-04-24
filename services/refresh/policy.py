"""
Refresh policy: staleness thresholds and mode decisions.
Single source of truth for all refresh-related constants.
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

RefreshMode = Literal["full", "stats_only", "background_full"]

# Unified thresholds (replaces AUTO_UPDATE_HOURS, UPDATE_THRESHOLD_HOURS, STALE_THRESHOLD)
STALE_THRESHOLD = timedelta(hours=1)
BACKGROUND_THRESHOLD = timedelta(hours=2)


def is_stale(last_api_update, threshold: timedelta = STALE_THRESHOLD) -> bool:
    """Return True if last_api_update is older than threshold (or None)."""
    if last_api_update is None:
        return True
    ts = last_api_update
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts) > threshold


def needs_blocking_refresh(last_api_update) -> bool:
    """True → caller should await a full refresh before rendering."""
    return is_stale(last_api_update, STALE_THRESHOLD)


def needs_background_refresh(last_api_update) -> bool:
    """True → background updater should pick this user up."""
    return is_stale(last_api_update, BACKGROUND_THRESHOLD)
