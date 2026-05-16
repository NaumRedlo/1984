from sqlalchemy import Column, Integer, DateTime
from db.database import Base


class Season(Base):
    __tablename__ = 'seasons'

    id = Column(Integer, primary_key=True, autoincrement=True)
    number = Column(Integer, nullable=False, unique=True)
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=True)
    is_active = Column(Integer, nullable=False, default=1)

    def __repr__(self):
        return f"<Season(number={self.number}, active={self.is_active})>"
