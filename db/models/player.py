from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, Float, Integer, String, Text

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
    "level",
    "join_date",
    "grade_count_s",
    "grade_count_ss",
    "best_scores_baseline_at",
    "profile_opens_date",
    "profile_opens_count",
    "profile_opens_best",
    "compare_uses",
    "active_day",
    "active_streak",
    "active_streak_best",
    "playcount_week_anchor",
    "playcount_week_anchor_at",
    "week_plays_best",
    "comeback_done",
    "app_profile",
    "app_profile_at",
    "last_seen_at",
)

OWN = SHARED[:16]
PROGRESS = SHARED[16:]

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

    level = Column(Integer, nullable=True)
    join_date = Column(DateTime, nullable=True)
    grade_count_s = Column(Integer, nullable=True)
    grade_count_ss = Column(Integer, nullable=True)
    best_scores_baseline_at = Column(DateTime, nullable=True)
    profile_opens_date = Column(Date, nullable=True)
    profile_opens_count = Column(Integer, nullable=True)
    profile_opens_best = Column(Integer, nullable=True)
    compare_uses = Column(Integer, nullable=True)
    active_day = Column(Date, nullable=True)
    active_streak = Column(Integer, nullable=True)
    active_streak_best = Column(Integer, nullable=True)
    playcount_week_anchor = Column(Integer, nullable=True)
    playcount_week_anchor_at = Column(DateTime, nullable=True)
    week_plays_best = Column(Integer, nullable=True)
    comeback_done = Column(Boolean, nullable=True)
    app_profile = Column(Text, nullable=True)
    app_profile_at = Column(DateTime, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)

    pinned_chat_id = Column(BigInteger, nullable=True)
    pinned_at = Column(DateTime, nullable=True)

    accept_videos = Column(String(16), nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
