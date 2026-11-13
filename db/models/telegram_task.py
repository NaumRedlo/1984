from sqlalchemy import JSON, Column, DateTime, Index, Integer, LargeBinary, String, Text

from db.database import Base
from utils.timeutils import utcnow


class TelegramTask(Base):
    __tablename__ = "telegram_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kind = Column(String(32), nullable=False)
    payload = Column(JSON, nullable=False)
    blob = Column(LargeBinary, nullable=True)
    status = Column(String(16), nullable=False, default="pending")
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=False, default=utcnow)
    expires_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    answer = Column(JSON, nullable=True)
    answer_blob = Column(LargeBinary, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    done_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_telegram_tasks_due", "status", "next_attempt_at"),)
