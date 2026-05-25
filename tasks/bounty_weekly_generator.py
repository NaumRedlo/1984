"""Weekly bounty pool generator — fires Monday 00:00 MSK.

Plan: unified-giggling-tiger.

Background loop pattern is copied from tasks/bounty_weekly.py:weekly_digest_loop
(same TZ, same shutdown handling), but fires at hour=0 instead of 10. On
startup, if no active WeeklyBountyPool exists OR the current one already
ended, runs a one-shot bootstrap so a freshly-deployed bot is never left
without a pool.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot
from sqlalchemy import select

from config.settings import TIMEZONE
from db.database import get_db_session
from db.models.weekly_bounty_pool import WeeklyBountyPool
from services.bounty.weekly_generator import generate_weekly_pool

logger = logging.getLogger(__name__)


async def _bootstrap_if_needed() -> None:
    """Run one generation cycle if no active pool exists or it expired."""
    now = datetime.utcnow()
    async with get_db_session() as session:
        active = (await session.execute(
            select(WeeklyBountyPool).where(WeeklyBountyPool.is_active == 1)
        )).scalars().first()
        needs = active is None or (active.ends_at and active.ends_at <= now)
        if not needs:
            return
        logger.info(
            "weekly_generator bootstrap: no active pool or pool ended "
            f"(active={active!r}); running generate_weekly_pool now"
        )
        try:
            await generate_weekly_pool(session)
            await session.commit()
        except Exception:
            logger.error("weekly_generator bootstrap failed", exc_info=True)


async def weekly_generator_loop(bot: Bot, shutdown_event: asyncio.Event) -> None:
    """Wait until next Monday 00:00 local time, generate pool, repeat."""
    tz = ZoneInfo(TIMEZONE)

    # One-shot bootstrap on startup so the bot is never poolless.
    try:
        await _bootstrap_if_needed()
    except Exception:
        logger.error("weekly_generator_loop bootstrap raised", exc_info=True)

    while not shutdown_event.is_set():
        now = datetime.now(tz)
        days_until_monday = (7 - now.weekday()) % 7 or 7
        target = (now + timedelta(days=days_until_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        wait_seconds = (target - now).total_seconds()
        logger.info(
            f"weekly_generator: next run in {wait_seconds/3600:.1f}h at "
            f"{target.strftime('%Y-%m-%d %H:%M %Z')}"
        )

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=wait_seconds)
            break  # shutdown signal received
        except asyncio.TimeoutError:
            pass

        if shutdown_event.is_set():
            break

        try:
            async with get_db_session() as session:
                pool = await generate_weekly_pool(session)
                await session.commit()
            logger.info(
                f"weekly_generator: pool w{pool.week_number} generated"
            )
        except Exception:
            logger.error(
                "weekly_generator: generation failed; will retry next cycle",
                exc_info=True,
            )
