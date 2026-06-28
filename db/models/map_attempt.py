from datetime import datetime, timezone

from sqlalchemy import Column, Integer, BigInteger, Float, String, Boolean, DateTime, ForeignKey, Index

from db.database import Base


class UserMapAttempt(Base):
    __tablename__ = "user_map_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    score_id = Column(BigInteger, unique=True, nullable=False)
    beatmap_id = Column(Integer, nullable=False, index=True)
    beatmapset_id = Column(Integer, nullable=True)
    score = Column(BigInteger, nullable=True)
    pp = Column(Float, nullable=False)
    accuracy = Column(Float, nullable=True)
    max_combo = Column(Integer, nullable=True)
    rank = Column(String(10), nullable=True)
    mods = Column(String(255), nullable=True)
    artist = Column(String(255), nullable=True)
    title = Column(String(255), nullable=True)
    version = Column(String(255), nullable=True)
    creator = Column(String(255), nullable=True)
    star_rating = Column(Float, nullable=True)
    # Per-play fields for live title evaluation (titles = best_scores ∪ attempts).
    bpm = Column(Float, nullable=True)
    length = Column(Integer, nullable=True)            # seconds (map total_length)
    map_max_combo = Column(Integer, nullable=True)
    count_300 = Column(Integer, nullable=True)         # for completion % on fails ("Last Note")
    count_100 = Column(Integer, nullable=True)
    count_50 = Column(Integer, nullable=True)
    count_miss = Column(Integer, nullable=True)
    total_objects = Column(Integer, nullable=True)      # map object count (circles+sliders+spinners)
    is_fc = Column(Boolean, nullable=True)             # API perfect-combo flag (primary FC signal)
    status = Column(String(20), nullable=True)         # beatmap status (graveyard/ranked/loved/…)
    ranked_date = Column(DateTime, nullable=True)      # beatmapset ranked date (for the "12y+" title)
    passed = Column(Boolean, nullable=True)            # False = a logged fail
    played_at = Column(DateTime, nullable=True)        # real play time (score ended_at)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("ix_user_map_attempts_beatmap_user_pp", "beatmap_id", "user_id", "pp"),
    )

    def __repr__(self):
        return f"<UserMapAttempt(id={self.id}, user_id={self.user_id}, beatmap_id={self.beatmap_id}, pp={self.pp})>"
