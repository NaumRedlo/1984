from sqlalchemy import Column, Integer, String, DateTime, Text
from sqlalchemy.sql import func
from db.database import Base


class BskMlRun(Base):
    __tablename__ = "bsk_ml_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ran_at = Column(DateTime, nullable=False, server_default=func.now())
    rounds_used = Column(Integer, nullable=False, default=0)
    maps_updated = Column(Integer, nullable=False, default=0)
    maps_skipped = Column(Integer, nullable=False, default=0)
    status = Column(String(32), nullable=False, default="ok")
    triggered_by = Column(String(32), nullable=False, default="scheduler")
    notes = Column(Text, nullable=True)
