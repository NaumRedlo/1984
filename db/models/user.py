from sqlalchemy import Column, Integer, BigInteger, String, DateTime, func
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    osu_username = Column(String(50), nullable=True)
    osu_user_id = Column(Integer, nullable=True)
    hps_points = Column(Integer, default=0)
    rank = Column(String(30), default="Candidate")
    bounties_participated = Column(Integer, default=0)
    last_active_bounty_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<User(id={self.id}, tg={self.telegram_id}, osu='{self.osu_username}', osu_id={self.osu_user_id}, HP={self.hps_points}, rank='{self.rank}')>"
