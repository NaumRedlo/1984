from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String

from db.database import Base
from utils.timeutils import utcnow


class StoryProgress(Base):
    __tablename__ = "story_progress"

    player_id = Column(Integer, ForeignKey("players.id"), primary_key=True)
    chapter = Column(String(64), nullable=False)
    step = Column(String(64), nullable=False)
    flags = Column(JSON, nullable=False, default=list)
    task = Column(JSON, nullable=True)
    done = Column(JSON, nullable=False, default=list)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
