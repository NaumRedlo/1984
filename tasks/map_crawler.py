"""Background loop for the map crawler.

Mirrors the patterns used by tasks/bounty_weekly_generator.py:
  - reads interval from BotSettings (`map_crawler_interval_hours`)
  - one-shot at startup ONLY if enabled (so newly deployed bots can do
    a first warm-up pass without waiting an interval)
  - sleeps on a shutdown_event so the loop exits cleanly
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot

from services.map_import.crawler import read_config, run_one_cycle

logger = logging.getLogger(__name__)


async def map_crawler_loop(
    bot: Bot,
    shutdown_event: asyncio.Event,
    osu_api_client=None,
) -> None:
    """Run the crawler indefinitely, respecting BotSettings."""
    if osu_api_client is None:
        logger.warning("map_crawler_loop: no osu_api_client — loop disabled")
        return

    # One initial cycle so a freshly enabled crawler reacts immediately.
    try:
        cfg = await read_config()
        if cfg.enabled:
            logger.info("map_crawler: initial warm-up cycle")
            report = await run_one_cycle(osu_api_client, config=cfg)
            logger.info(
                "map_crawler initial: found=%d ingested=%d notes=%s",
                report.found_candidates,
                len(report.ingested_ids),
                report.notes,
            )
    except Exception:
        logger.error("map_crawler initial cycle failed", exc_info=True)

    while not shutdown_event.is_set():
        try:
            cfg = await read_config()
        except Exception:
            logger.error("map_crawler: read_config failed", exc_info=True)
            cfg = None

        # If disabled, sleep a short fixed window before re-checking — admin
        # may flip the switch between checks.
        if cfg is None or not cfg.enabled:
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=300)
                break
            except asyncio.TimeoutError:
                continue

        # Sleep the configured interval.
        wait_seconds = cfg.interval_hours * 3600
        logger.info(
            "map_crawler: next cycle in %.1fh (budget=%d, zones=%s)",
            wait_seconds / 3600, cfg.budget, cfg.zones,
        )
        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=wait_seconds)
            break
        except asyncio.TimeoutError:
            pass
        if shutdown_event.is_set():
            break

        # Re-read config in case it changed during sleep.
        try:
            cfg = await read_config()
        except Exception:
            logger.error("map_crawler: read_config (post-sleep) failed", exc_info=True)
            continue
        if not cfg.enabled:
            continue

        try:
            t0 = datetime.now(timezone.utc)
            report = await run_one_cycle(osu_api_client, config=cfg)
            took = (datetime.now(timezone.utc) - t0).total_seconds()
            logger.info(
                "map_crawler cycle done in %.1fs: found=%d ingested=%d added=%s",
                took, report.found_candidates,
                len(report.ingested_ids), report.added_per_pool,
            )
        except Exception:
            logger.error("map_crawler cycle failed", exc_info=True)
