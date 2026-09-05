from sqlalchemy import Column, Integer, String, BigInteger, DateTime, Date, Float, Boolean, LargeBinary, UniqueConstraint
from datetime import datetime, timezone
from db.database import Base

class User(Base):
    __tablename__ = 'users'

    __table_args__ = (
        UniqueConstraint('chat_id', 'telegram_id', name='uq_users_chat_telegram'),
        UniqueConstraint('chat_id', 'osu_user_id', name='uq_users_chat_osu'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, nullable=False, index=True)
    telegram_id = Column(BigInteger, nullable=False, index=True)
    osu_username = Column(String(255), nullable=False)
    osu_user_id = Column(Integer, nullable=True, index=True)

    player_pp = Column(Integer, default=0, nullable=True)
    global_rank = Column(Integer, default=0, nullable=True)
    country = Column(String(2), default="XX", nullable=True)
    accuracy = Column(Float, default=0.0, nullable=True)
    play_count = Column(Integer, default=0, nullable=True)
    play_time = Column(Integer, default=0, nullable=True)
    ranked_score = Column(BigInteger, default=0, nullable=True)
    total_hits = Column(BigInteger, default=0, nullable=True)
    total_score = Column(BigInteger, default=0, nullable=True)
    is_supporter = Column(Boolean, nullable=True)
    was_supporter = Column(Boolean, default=False, nullable=True)

    best_scores_baseline_at = Column(DateTime, nullable=True)

    share_replays = Column(Boolean, default=False, nullable=True)

    render_size = Column(String(16), nullable=True)
    render_fps = Column(Integer, nullable=True)
    render_mute = Column(Boolean, nullable=True)
    render_skin = Column(String(64), nullable=True)
    render_background = Column(Boolean, nullable=True)
    render_bare = Column(Boolean, nullable=True)

    render_effects = Column(String(128), nullable=True)

    render_music = Column(Integer, nullable=True)
    render_hitsounds = Column(Integer, nullable=True)

    render_map_hitsounds = Column(Boolean, nullable=True)

    render_dim = Column(Integer, nullable=True)

    render_meter = Column(Integer, nullable=True)

    render_volume = Column(Integer, nullable=True)

    render_leaderboard = Column(Boolean, nullable=True)

    render_cursor = Column(Integer, nullable=True)

    render_blur = Column(Integer, nullable=True)

    heavy_renders = Column(Integer, nullable=True)
    heavy_renders_on = Column(String(10), nullable=True)
    avatar_url = Column(String(512), nullable=True)
    cover_url = Column(String(512), nullable=True)
    avatar_data = Column(LargeBinary, nullable=True)
    cover_data = Column(LargeBinary, nullable=True)

    hps_points = Column(Integer, default=0, nullable=False)
    rank = Column(String(50), default='Candidate', nullable=False)
    season_bonus_hps = Column(Integer, default=0, nullable=False)
    bounties_participated = Column(Integer, default=0, nullable=False)
    duel_wins = Column(Integer, default=0, nullable=False)
    duel_losses = Column(Integer, default=0, nullable=False)
    last_active_bounty_id = Column(String(50), nullable=True)
    active_title_code = Column(String(50), nullable=True)

    profile_opens_date = Column(Date, nullable=True)
    profile_opens_count = Column(Integer, default=0, nullable=True)
    profile_opens_best = Column(Integer, default=0, nullable=True)
    compare_uses = Column(Integer, default=0, nullable=True)
    active_day = Column(Date, nullable=True)
    active_streak = Column(Integer, default=0, nullable=True)
    active_streak_best = Column(Integer, default=0, nullable=True)
    playcount_week_anchor = Column(Integer, nullable=True)
    playcount_week_anchor_at = Column(DateTime, nullable=True)
    week_plays_best = Column(Integer, default=0, nullable=True)
    comeback_done = Column(Boolean, default=False, nullable=True)

    level = Column(Integer, default=0, nullable=True)
    join_date = Column(DateTime, nullable=True)
    grade_count_s = Column(Integer, default=0, nullable=True)
    grade_count_ss = Column(Integer, default=0, nullable=True)

    duel_user_aim = Column(Float, default=4.0, nullable=False)
    duel_user_speed = Column(Float, default=4.0, nullable=False)
    duel_user_acc = Column(Float, default=4.0, nullable=False)
    duel_user_cons = Column(Float, default=4.0, nullable=False)
    duel_skill_calculated_at = Column(DateTime, nullable=True)

    bp = Column(Integer, default=0, nullable=False)
    weekly_tier = Column(String(2), nullable=True)
    weekly_tier_set_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    last_api_update = Column(DateTime, nullable=True)

    last_full_update = Column(DateTime, nullable=True)
    last_unlink_at = Column(DateTime, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)

    first_approved_at = Column(DateTime, nullable=True)

    oauth_access_token = Column(String(512), nullable=True)
    oauth_refresh_token = Column(String(512), nullable=True)
    oauth_token_expiry = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<User(id={self.id}, tg={self.telegram_id}, osu='{self.osu_username}', osu_id={self.osu_user_id}, HP={self.hps_points}, rank='{self.rank}')>"
