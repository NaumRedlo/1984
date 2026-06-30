from sqlalchemy import Column, Integer, String, DateTime
from db.database import Base
from utils.timeutils import utcnow


class RenderCache(Base):
    """Maps a rendered replay (cache_key) to the Telegram file_id of its already
    uploaded video, so a repeat /render of the same replay+settings is re-sent
    instantly — no GPU wake, no danser render. Keyed by content, not by user."""

    __tablename__ = 'render_cache'

    id = Column(Integer, primary_key=True, autoincrement=True)
    cache_key = Column(String(255), unique=True, nullable=False, index=True)
    file_id = Column(String(512), nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)
