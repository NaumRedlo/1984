from sqlalchemy import BigInteger, Column, DateTime, Integer, String, Text

from db.database import Base
from utils.timeutils import utcnow

class Incident(Base):
    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(8), nullable=False, unique=True, index=True)
    area = Column(String(32), nullable=False, index=True)
    stage = Column(String(48), nullable=False)
    reason = Column(String(300), nullable=False)
    telegram_id = Column(BigInteger, nullable=True, index=True)
    chat_id = Column(BigInteger, nullable=True)
    detail = Column(Text, nullable=True)
    trace = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False, index=True)
