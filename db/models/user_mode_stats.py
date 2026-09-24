from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, Float, Integer, UniqueConstraint

from db.database import Base


class UserModeStats(Base):
    """A player's standing in taiko, catch or mania — osu!standard's lives on the user row itself."""

    __tablename__ = "user_mode_stats"
    __table_args__ = (UniqueConstraint("osu_user_id", "ruleset", name="uq_user_mode_stats"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    osu_user_id = Column(BigInteger, nullable=False, index=True)
    ruleset = Column(Integer, nullable=False)
    pp = Column(Float, nullable=True)
    global_rank = Column(Integer, nullable=True)
    country_rank = Column(Integer, nullable=True)
    accuracy = Column(Float, nullable=True)
    play_count = Column(Integer, nullable=True)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
