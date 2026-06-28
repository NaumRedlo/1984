from sqlalchemy import Column, Integer, String, BigInteger, DateTime, Date, Float, Boolean, LargeBinary, UniqueConstraint
from datetime import datetime, timezone
from db.database import Base


class User(Base):
    __tablename__ = 'users'
    # Multi-tenant: every user row is scoped to the Telegram group (chat_id) it
    # was registered in. Identity is unique *within* a group, not globally — the
    # same person / osu! account can exist independently in several groups.
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
    is_supporter = Column(Boolean, nullable=True)   # current osu!supporter flag (profile badge)
    was_supporter = Column(Boolean, default=False, nullable=True)  # latched: ever a supporter ("Volunteer" is permanent)

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

    # Wave-4 title logging subsystems (counters / activity / weekly play deltas).
    # Day fields are UTC dates (the bot tracks no per-user timezone).
    profile_opens_date = Column(Date, nullable=True)        # day of the running open-count
    profile_opens_count = Column(Integer, default=0, nullable=True)   # opens so far today
    profile_opens_best = Column(Integer, default=0, nullable=True)    # most opens in a day ("Still Here")
    compare_uses = Column(Integer, default=0, nullable=True)          # /compare-on-others count ("Informant")
    active_day = Column(Date, nullable=True)                # last UTC day with activity
    active_streak = Column(Integer, default=0, nullable=True)         # current consecutive active days
    active_streak_best = Column(Integer, default=0, nullable=True)    # best streak ("Sleepless Watch")
    playcount_week_anchor = Column(Integer, nullable=True)  # play_count at the current week window start
    playcount_week_anchor_at = Column(DateTime, nullable=True)        # when that window opened (naive UTC)
    week_plays_best = Column(Integer, default=0, nullable=True)       # best plays-in-a-week ("Stakhanovite")
    comeback_done = Column(Boolean, default=False, nullable=True)     # returned after 180d+ ("quit w")

    duel_user_aim = Column(Float, default=4.0, nullable=False)
    duel_user_speed = Column(Float, default=4.0, nullable=False)
    duel_user_acc = Column(Float, default=4.0, nullable=False)
    duel_user_cons = Column(Float, default=4.0, nullable=False)
    duel_skill_calculated_at = Column(DateTime, nullable=True)

    # Bounty system v2 (Plan: unified-giggling-tiger):
    #   bp                  — placeholder currency, not consumed in MVP.
    #   weekly_tier         — snapshot from get_tier_for_hp(hps_points),
    #                         frozen Monday 00:00 MSK by the weekly generator.
    #   weekly_tier_set_at  — timestamp of that snapshot.
    bp = Column(Integer, default=0, nullable=False)
    weekly_tier = Column(String(2), nullable=True)
    weekly_tier_set_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    last_api_update = Column(DateTime, nullable=True)
    last_unlink_at = Column(DateTime, nullable=True)
    last_seen_at = Column(DateTime, nullable=True)

    # Anchor for the bootstrap multiplier B(t) in hp_calculator.
    # Set to submission.reviewed_at (or now()) on the user's FIRST approved
    # submission — see review.py / replay.py / bounty_auto_checker.py.
    # NULL means the user has no approvals yet → B(t) treats them as day 0.
    first_approved_at = Column(DateTime, nullable=True)

    oauth_access_token = Column(String(512), nullable=True)
    oauth_refresh_token = Column(String(512), nullable=True)
    oauth_token_expiry = Column(DateTime, nullable=True)


    def __repr__(self):
        return f"<User(id={self.id}, tg={self.telegram_id}, osu='{self.osu_username}', osu_id={self.osu_user_id}, HP={self.hps_points}, rank='{self.rank}')>"
