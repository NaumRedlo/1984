from sqlalchemy import Column, Integer, String, BigInteger, DateTime, Float, ForeignKey, Boolean
from datetime import datetime, timezone

from db.database import Base


def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Bounty(Base):
    __tablename__ = 'bounties'

    id = Column(Integer, primary_key=True, autoincrement=True)
    bounty_id = Column(String, unique=True, nullable=False, index=True)
    bounty_type = Column(String, default="First FC", nullable=False)
    title = Column(String, nullable=False)
    beatmap_id = Column(Integer, nullable=False)
    beatmapset_id = Column(Integer, nullable=True)
    beatmap_title = Column(String, nullable=False)
    mapper_id = Column(Integer, nullable=True)
    mapper_name = Column(String, nullable=True)
    mapper_avatar_url = Column(String, nullable=True)
    star_rating = Column(Float, nullable=False)
    drain_time = Column(Integer, nullable=False)
    min_accuracy = Column(Float, nullable=True)
    required_mods = Column(String, nullable=True)
    max_misses = Column(Integer, nullable=True)
    min_rank = Column(String, nullable=True)
    min_hp = Column(Integer, nullable=True)
    max_participants = Column(Integer, nullable=True)
    cs = Column(Float, default=0.0)
    od = Column(Float, default=0.0)
    ar = Column(Float, default=0.0)
    hp_drain = Column(Float, default=0.0)
    bpm = Column(Float, default=0.0)
    max_combo = Column(Integer, default=0)
    status = Column(String, default="active", nullable=False)
    created_by = Column(BigInteger, nullable=False)
    deadline = Column(DateTime, nullable=True)
    last_edited_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)
    reminder_sent = Column(Boolean, default=False, nullable=False)
    # Weekly tier-pool fields (Plan: unified-giggling-tiger).  Manual bounties
    # have source='manual', tier=NULL, week_id=NULL.  Auto-generated bounties
    # carry tier ∈ {'C','B','A','Open'} and week_id referencing weekly_bounty_pool.
    source = Column(String, default="manual", nullable=False)
    tier = Column(String, nullable=True)
    week_id = Column(Integer, nullable=True)
    # JSON-serialised extra conditions not covered by the legacy columns
    # (min_accuracy, required_mods, max_misses).  Example: {"max_ur": 75},
    # {"min_combo_pct": 0.8}.  Read with json.loads in the auto-checker only if
    # legacy columns are insufficient — generator mirrors them when possible.
    conditions = Column(String, nullable=True)

    def __repr__(self):
        return f"<Bounty(id={self.id}, bounty_id='{self.bounty_id}', title='{self.title}', status='{self.status}')>"


class Submission(Base):
    __tablename__ = 'submissions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    bounty_id = Column(String, ForeignKey('bounties.bounty_id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    telegram_id = Column(BigInteger, nullable=False)
    accuracy = Column(Float, nullable=True)
    max_combo = Column(Integer, nullable=True)
    misses = Column(Integer, nullable=True)
    mods = Column(String, nullable=True)
    score_rank = Column(String, nullable=True)
    n_300 = Column(Integer, nullable=True)
    n_100 = Column(Integer, nullable=True)
    n_50 = Column(Integer, nullable=True)
    ur_est = Column(Float, nullable=True)
    status = Column(String, default="pending", nullable=False, index=True)
    result_type = Column(String, nullable=True)
    hp_awarded = Column(Integer, nullable=True)
    reviewed_by = Column(BigInteger, nullable=True)
    review_comment = Column(String, nullable=True)
    submitted_at = Column(DateTime, default=_utcnow, nullable=False)
    reviewed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<Submission(id={self.id}, bounty='{self.bounty_id}', user={self.user_id}, status='{self.status}')>"
