from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, BigInteger, Float, String, DateTime, ForeignKey,
    Index, UniqueConstraint,
)

from db.database import Base


class LeaderboardSnapshot(Base):
    """A player's metric values as of the START of a leaderboard period.

    The delta leaderboard ranks by growth over the current week: growth =
    current value on `users` minus the anchor stored here. One row per
    (tenant, user, period); written once when the period opens (see
    services/leaderboard/snapshots.py) and never mutated afterwards.

    `prev_positions` carries the *final* standings of the period that just
    closed, so the ▲/▼ movement column needs no historical recomputation.
    """
    __tablename__ = "leaderboard_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_chat_id = Column(BigInteger, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # ISO week in Moscow time, e.g. "2026-W30" (services/leaderboard/periods.py).
    period_key = Column(String(16), nullable=False, index=True)
    captured_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    # Metric anchors — mirror the User columns the leaderboard ranks on.
    player_pp = Column(Float, nullable=True)
    accuracy = Column(Float, nullable=True)
    play_count = Column(Integer, nullable=True)
    play_time = Column(Integer, nullable=True)
    ranked_score = Column(BigInteger, nullable=True)
    total_hits = Column(BigInteger, nullable=True)

    # JSON {category_key: position} — where this player finished LAST period.
    # NULL/absent means "no prior standing" -> the card shows `new`, not ▲0.
    prev_positions = Column(String(512), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_chat_id", "user_id", "period_key",
                         name="uq_leaderboard_snapshot_period"),
        Index("ix_leaderboard_snapshots_tenant_period", "tenant_chat_id", "period_key"),
    )

    def __repr__(self):
        return (f"<LeaderboardSnapshot(user_id={self.user_id}, "
                f"period={self.period_key}, pp={self.player_pp})>")
