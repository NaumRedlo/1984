from sqlalchemy import Column, Integer, String, BigInteger, DateTime, Float, ForeignKey
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
    beatmap_title = Column(String, nullable=False)
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

    def __repr__(self):
        return f"<Bounty(id={self.id}, bounty_id='{self.bounty_id}', title='{self.title}', status='{self.status}')>"


class Submission(Base):
    __tablename__ = 'submissions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    bounty_id = Column(String, ForeignKey('bounties.bounty_id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    telegram_id = Column(BigInteger, nullable=False)
    accuracy = Column(Float, nullable=True)
    max_combo = Column(Integer, nullable=True)
    misses = Column(Integer, nullable=True)
    mods = Column(String, nullable=True)
    score_rank = Column(String, nullable=True)
    status = Column(String, default="pending", nullable=False)
    result_type = Column(String, nullable=True)
    hp_awarded = Column(Integer, nullable=True)
    reviewed_by = Column(BigInteger, nullable=True)
    review_comment = Column(String, nullable=True)
    submitted_at = Column(DateTime, default=_utcnow, nullable=False)
    reviewed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<Submission(id={self.id}, bounty='{self.bounty_id}', user={self.user_id}, status='{self.status}')>"
