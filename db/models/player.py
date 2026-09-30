from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Float, Integer, String

from db.database import Base

SHARED = (
    "osu_username",
    "player_pp",
    "global_rank",
    "country",
    "accuracy",
    "play_count",
    "play_time",
    "ranked_score",
    "total_hits",
    "total_score",
    "is_supporter",
    "was_supporter",
    "avatar_url",
    "cover_url",
    "active_title_code",
    "share_replays",
)

class Player(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, autoincrement=True)
    osu_user_id = Column(Integer, nullable=False, unique=True, index=True)
    telegram_id = Column(BigInteger, nullable=True, unique=True, index=True)

    osu_username = Column(String(255), nullable=False)
    player_pp = Column(Integer, nullable=True)
    global_rank = Column(Integer, nullable=True)
    country = Column(String(2), nullable=True)
    accuracy = Column(Float, nullable=True)
    play_count = Column(Integer, nullable=True)
    play_time = Column(Integer, nullable=True)
    ranked_score = Column(BigInteger, nullable=True)
    total_hits = Column(BigInteger, nullable=True)
    total_score = Column(BigInteger, nullable=True)
    is_supporter = Column(Boolean, nullable=True)
    was_supporter = Column(Boolean, nullable=True)
    avatar_url = Column(String(512), nullable=True)
    cover_url = Column(String(512), nullable=True)
    active_title_code = Column(String(50), nullable=True)
    share_replays = Column(Boolean, nullable=True)

    pinned_chat_id = Column(BigInteger, nullable=True)
    pinned_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
