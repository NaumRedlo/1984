"""Periodic job: flip past-deadline active bounties to ``expired``.

Reads /bountylist used to mutate rows on the fly; that side-effect is now
isolated in this background task so list/detail handlers stay read-only.
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select, update

from db.database import get_db_session
from db.models.bounty import Bounty, Submission
from utils.logger import get_logger

logger = get_logger("tasks.bounty_expirer")

EXPIRE_INTERVAL_SECONDS = 300  # 5 minutes is plenty for deadline granularity


async def _expire_overdue_once() -> int:
    """Mark every active bounty past its deadline as expired.

    Also rejects any tracking submissions on those bounties — the player did
    not complete the challenge before the deadline.

    Returns count of expired bounties.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with get_db_session() as session:
        # 1. Find bounties to expire (need IDs for submission cleanup).
        overdue = (await session.execute(
            select(Bounty.bounty_id).where(
                Bounty.status == "active",
                Bounty.deadline.is_not(None),
                Bounty.deadline < now,
            )
        )).scalars().all()

        if not overdue:
            return 0

        # 2. Expire the bounties.
        await session.execute(
            update(Bounty)
            .where(Bounty.bounty_id.in_(overdue))
            .values(status="expired", closed_at=now)
            .execution_options(synchronize_session=False)
        )

        # 3. Reject tracking submissions that never qualified.
        await session.execute(
            update(Submission)
            .where(
                Submission.bounty_id.in_(overdue),
                Submission.status == "tracking",
            )
            .values(status="rejected", review_comment="Баунти истёк")
            .execution_options(synchronize_session=False)
        )

        await session.commit()
        return len(overdue)


async def bounty_expirer_loop(shutdown_event: asyncio.Event) -> None:
    """Background loop: every ~5 minutes, expire overdue bounties."""
    while not shutdown_event.is_set():
        try:
            n = await _expire_overdue_once()
            if n:
                logger.info(f"Expired {n} overdue bount{'y' if n == 1 else 'ies'}")
        except Exception as e:
            logger.error(f"Bounty expirer iteration failed: {e}", exc_info=True)

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=EXPIRE_INTERVAL_SECONDS)
            break
        except asyncio.TimeoutError:
            continue
