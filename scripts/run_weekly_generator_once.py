"""Run the weekly bounty pool generator once and commit.

Used to:
  * smoke-test the generator on real production data without waiting for
    Monday 00:00 MSK,
  * regenerate the current week's pool after manually fixing bsk_map_pool
    rows (e.g. enabling more maps).

Be aware: this commits.  It closes the currently-active WeeklyBountyPool +
all its auto-generated bounties, then inserts a fresh set.  Manual bounties
(source='manual') are NEVER touched.

Run from project root:
    python3 -m scripts.run_weekly_generator_once
"""

from __future__ import annotations

import asyncio
import logging

from db.database import engine, get_db_session
from db.migrations import run_all_migrations
from services.bounty.weekly_generator import generate_weekly_pool

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("run_weekly_generator_once")


async def main() -> None:
    await run_all_migrations(engine)

    async with get_db_session() as session:
        pool = await generate_weekly_pool(session)
        await session.commit()

    logger.info(
        f"Done. Pool id={pool.id} week={pool.week_number} "
        f"started_at={pool.started_at.isoformat()} "
        f"ends_at={pool.ends_at.isoformat()}"
    )


if __name__ == "__main__":
    asyncio.run(main())
