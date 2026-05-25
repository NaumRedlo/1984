"""WeeklyBountyPool — one row per weekly bounty cycle.

Plan: unified-giggling-tiger.

The weekly generator (tasks/bounty_weekly_generator.py) inserts a row each
Monday 00:00 MSK, populating tier slots (9 maps × C/B/A/Open = 36 bounties).
The previous active row is flipped to is_active=0 and its bounties are
expired.

`Bounty.week_id` references this id (logical FK, not enforced by SQLite).
"""

from datetime import datetime, timezone

from sqlalchemy import Column, Integer, DateTime

from db.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class WeeklyBountyPool(Base):
    __tablename__ = "weekly_bounty_pool"

    id = Column(Integer, primary_key=True, autoincrement=True)
    week_number = Column(Integer, nullable=False)
    started_at = Column(DateTime, default=_utcnow, nullable=False)
    ends_at = Column(DateTime, nullable=False)
    is_active = Column(Integer, default=1, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<WeeklyBountyPool(id={self.id}, week={self.week_number}, "
            f"active={self.is_active})>"
        )
