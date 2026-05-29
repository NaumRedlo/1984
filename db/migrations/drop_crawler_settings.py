"""Remove BotSettings rows belonging to the removed map crawler.

2026-05-29: the autonomous beatmap crawler was retired. Its config keys
in `bot_settings` (`map_crawler_enabled`, `map_crawler_budget`,
`map_crawler_interval_hours`, `map_crawler_last_run`,
`map_crawler_last_report`, `map_crawler_zones`) are no longer read by any
code path — drop them so admin dumps of the settings table don't show
dead state.

Idempotent: deletes only matching keys, succeeds whether they exist or not.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_CRAWLER_KEYS = (
    "map_crawler_enabled",
    "map_crawler_budget",
    "map_crawler_interval_hours",
    "map_crawler_last_run",
    "map_crawler_last_report",
    "map_crawler_zones",
)


async def run_drop_crawler_settings_migration(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        # bot_settings may not exist on very fresh DBs that skipped earlier
        # migrations — guard with a sqlite_master check.
        exists = (await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bot_settings'"
        ))).first()
        if not exists:
            logger.info("drop_crawler_settings: bot_settings table missing, skipping")
            return

        placeholders = ",".join(f":k{i}" for i in range(len(_CRAWLER_KEYS)))
        params = {f"k{i}": k for i, k in enumerate(_CRAWLER_KEYS)}
        result = await conn.execute(
            text(f"DELETE FROM bot_settings WHERE key IN ({placeholders})"),
            params,
        )
        n = result.rowcount or 0
        if n:
            logger.info(f"drop_crawler_settings: removed {n} stale crawler config rows")
