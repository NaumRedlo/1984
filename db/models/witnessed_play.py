from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint

from db.database import Base

class WitnessedPlay(Base):
    __tablename__ = "witnessed_plays"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    replay_hash = Column(String(32), nullable=False)
    beatmap_md5 = Column(String(32), nullable=False)
    beatmap_id = Column(Integer, nullable=True)
    beatmapset_id = Column(Integer, nullable=True)
    artist = Column(String(255), nullable=True)
    title = Column(String(255), nullable=True)
    version = Column(String(255), nullable=True)
    creator = Column(String(255), nullable=True)
    mods = Column(String(255), nullable=True)
    score = Column(BigInteger, nullable=False, default=0)
    accuracy = Column(Float, nullable=False, default=0.0)
    max_combo = Column(Integer, nullable=False, default=0)
    count_300 = Column(Integer, nullable=False, default=0)
    count_100 = Column(Integer, nullable=False, default=0)
    count_50 = Column(Integer, nullable=False, default=0)
    count_geki = Column(Integer, nullable=False, default=0)
    count_katu = Column(Integer, nullable=False, default=0)
    count_miss = Column(Integer, nullable=False, default=0)
    rank = Column(String(10), nullable=False, default="F")
    passed = Column(Boolean, nullable=False, default=False)
    star_rating = Column(Float, nullable=True)
    bpm = Column(Float, nullable=True)
    length = Column(Integer, nullable=True)
    map_max_combo = Column(Integer, nullable=True)
    status = Column(String(20), nullable=True)
    pp_estimated = Column(Float, nullable=True)
    played_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("player_id", "replay_hash", name="uq_witnessed_plays_player_replay"),
        Index("ix_witnessed_plays_player_played", "player_id", "played_at"),
    )

    def __repr__(self):
        return f"<WitnessedPlay(id={self.id}, player_id={self.player_id}, beatmap_md5={self.beatmap_md5}, passed={self.passed})>"
