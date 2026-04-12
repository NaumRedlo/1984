from sqlalchemy import Column, Integer, BigInteger, Float, String, DateTime, ForeignKey, Index
from datetime import datetime, timezone
from db.database import Base


class UserBestScore(Base):
    __tablename__ = 'user_best_scores'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    score_id = Column(BigInteger, unique=True, nullable=False)
    beatmap_id = Column(Integer, nullable=False)
    beatmapset_id = Column(Integer, nullable=True)
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
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index('ix_user_best_scores_user_pp', 'user_id', 'pp'),
    )

    def __repr__(self):
        return f"<UserBestScore(id={self.id}, user_id={self.user_id}, pp={self.pp}, title='{self.title}')>"
