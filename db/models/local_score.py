from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint

from db.database import Base

class LocalScore(Base):
    __tablename__ = "local_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    beatmap_md5 = Column(String(32), nullable=False)
    beatmap_id = Column(Integer, nullable=False)
    beatmapset_id = Column(Integer, nullable=True)
    played_at = Column(DateTime, nullable=False)
    score = Column(BigInteger, nullable=False, default=0)
    max_combo = Column(Integer, nullable=False, default=0)
    count_300 = Column(Integer, nullable=False, default=0)
    count_100 = Column(Integer, nullable=False, default=0)
    count_50 = Column(Integer, nullable=False, default=0)
    count_geki = Column(Integer, nullable=False, default=0)
    count_katu = Column(Integer, nullable=False, default=0)
    count_miss = Column(Integer, nullable=False, default=0)
    perfect = Column(Boolean, nullable=False, default=False)
    mods = Column(Integer, nullable=False, default=0)
    star_rating = Column(Float, nullable=True)
    base_star_rating = Column(Float, nullable=True)
    ar = Column(Float, nullable=True)
    cs = Column(Float, nullable=True)
    od = Column(Float, nullable=True)
    hp = Column(Float, nullable=True)
    bpm = Column(Float, nullable=True)
    length = Column(Integer, nullable=True)
    objects = Column(Integer, nullable=True)
    status = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("player_id", "beatmap_md5", "played_at", "score", name="uq_local_scores_play"),
        Index("ix_local_scores_player_played", "player_id", "played_at"),
    )

    def __repr__(self):
        return f"<LocalScore(id={self.id}, player_id={self.player_id}, beatmap_id={self.beatmap_id}, score={self.score})>"
