from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey, UniqueConstraint
from datetime import datetime, timezone
from db.database import Base


class BskRating(Base):
    __tablename__ = 'bsk_ratings'
    __table_args__ = (
        UniqueConstraint('user_id', 'mode', name='uq_bsk_user_mode'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)

    mode = Column(String(10), nullable=False, default='casual')  # casual | ranked

    mu = Column(Float, default=1500.0, nullable=False)
    sigma = Column(Float, default=200.0, nullable=False)

    mechanical = Column(Float, default=0.0, nullable=False)
    precision = Column(Float, default=0.0, nullable=False)

    placement_matches_left = Column(Integer, default=10, nullable=False)

    wins = Column(Integer, default=0, nullable=False)
    losses = Column(Integer, default=0, nullable=False)

    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    def __repr__(self):
        return f"<BskRating(user={self.user_id}, mode={self.mode}, mu={self.mu:.1f}, σ={self.sigma:.1f})>"
