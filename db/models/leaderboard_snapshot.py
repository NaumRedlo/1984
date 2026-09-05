from datetime import datetime, timezone

from sqlalchemy import (
    Column, Integer, BigInteger, Float, String, DateTime, ForeignKey,
    Index, UniqueConstraint,
)

from db.database import Base

class LeaderboardSnapshot(Base):
    __tablename__ = "leaderboard_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_chat_id = Column(BigInteger, nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    period_key = Column(String(16), nullable=False, index=True)
    captured_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    player_pp = Column(Float, nullable=True)
    accuracy = Column(Float, nullable=True)
    play_count = Column(Integer, nullable=True)
    play_time = Column(Integer, nullable=True)
    ranked_score = Column(BigInteger, nullable=True)
    total_hits = Column(BigInteger, nullable=True)

    prev_positions = Column(String(512), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_chat_id", "user_id", "period_key",
                         name="uq_leaderboard_snapshot_period"),
        Index("ix_leaderboard_snapshots_tenant_period", "tenant_chat_id", "period_key"),
    )

    def __repr__(self):
        return (f"<LeaderboardSnapshot(user_id={self.user_id}, "
                f"period={self.period_key}, pp={self.player_pp})>")
