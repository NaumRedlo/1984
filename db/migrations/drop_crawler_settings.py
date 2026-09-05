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
