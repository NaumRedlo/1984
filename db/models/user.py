from sqlalchemy import Column, Integer, String, BigInteger, DateTime, Float, LargeBinary
from datetime import datetime, timezone
from db.database import Base


class User(Base):
    __tablename__ = 'users'

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    osu_username = Column(String(255), nullable=False)
    osu_user_id = Column(Integer, unique=True, nullable=True, index=True)

    player_pp = Column(Integer, default=0, nullable=True)
    global_rank = Column(Integer, default=0, nullable=True)
    country = Column(String(2), default="XX", nullable=True)
    accuracy = Column(Float, default=0.0, nullable=True)
    play_count = Column(Integer, default=0, nullable=True)
    play_time = Column(Integer, default=0, nullable=True)
    ranked_score = Column(BigInteger, default=0, nullable=True)
    total_hits = Column(BigInteger, default=0, nullable=True)
    total_score = Column(BigInteger, default=0, nullable=True)

    avatar_url = Column(String(512), nullable=True)
    cover_url = Column(String(512), nullable=True)
    avatar_data = Column(LargeBinary, nullable=True)
    cover_data = Column(LargeBinary, nullable=True)

    hps_points = Column(Integer, default=0, nullable=False)
    rank = Column(String(50), default='Candidate', nullable=False)
    bounties_participated = Column(Integer, default=0, nullable=False)
    last_active_bounty_id = Column(String(50), nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    last_api_update = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<User(id={self.id}, tg={self.telegram_id}, osu='{self.osu_username}', osu_id={self.osu_user_id}, HP={self.hps_points}, rank='{self.rank}')>"
