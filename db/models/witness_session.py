from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, UniqueConstraint

from db.database import Base

class WitnessSession(Base):
    __tablename__ = "witness_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=False)
    play_seconds = Column(Integer, nullable=False, default=0)
    plays = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("player_id", "started_at", name="uq_witness_sessions_player_start"),
        Index("ix_witness_sessions_player_ended", "player_id", "ended_at"),
    )

    def __repr__(self):
        return f"<WitnessSession(id={self.id}, player_id={self.player_id}, started_at={self.started_at}, play_seconds={self.play_seconds})>"
