from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from db.database import Base

class SharedReplay(Base):
    __tablename__ = "shared_replays"
    __table_args__ = (
        UniqueConstraint("player_id", "replay_hash", name="uq_shared_replays_player_hash"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    replay_hash = Column(String(32), nullable=False, index=True)
    beatmap_md5 = Column(String(32), nullable=False, index=True)
    size = Column(Integer, nullable=False)

    mods = Column(Integer, nullable=True)
    score = Column(BigInteger, nullable=True)
    combo = Column(Integer, nullable=True)
    count_300 = Column(Integer, nullable=True)
    count_100 = Column(Integer, nullable=True)
    count_50 = Column(Integer, nullable=True)
    count_miss = Column(Integer, nullable=True)
    perfect = Column(Boolean, nullable=True)
    played_at = Column(DateTime, nullable=True, index=True)

    artist = Column(String(255), nullable=True)
    title = Column(String(255), nullable=True)
    version = Column(String(255), nullable=True)
    beatmapset_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
