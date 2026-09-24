from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, Float, Integer, Text, UniqueConstraint

from db.database import Base


class TrackSnapshot(Base):
    """A player's standing in one mode as of their last update: what the next one is measured against.

    Kept per osu! account, not per chat member, so anyone's update can be asked for, as on osu!track."""

    __tablename__ = "track_snapshots"
    __table_args__ = (UniqueConstraint("osu_user_id", "ruleset", name="uq_track_snapshot"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    osu_user_id = Column(BigInteger, nullable=False, index=True)
    ruleset = Column(Integer, nullable=False, default=0)
    taken_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    pp = Column(Float, nullable=True)
    global_rank = Column(Integer, nullable=True)
    country_rank = Column(Integer, nullable=True)
    accuracy = Column(Float, nullable=True)
    play_count = Column(Integer, nullable=True)
    top = Column(Text, nullable=True)  # JSON: [[score_id, pp], ...] in the order of the top
