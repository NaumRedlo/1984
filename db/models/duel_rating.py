from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey, UniqueConstraint
from datetime import datetime, timezone
from db.database import Base


class DuelRating(Base):
    """Single-track TrueSkill rating for 1v1 duels.

    One Gaussian skill belief per (user, mode): ``mu`` (mean skill) and
    ``sigma`` (uncertainty). The leaderboard / division layer reads
    ``conservative = mu - 3*sigma`` so a player only climbs once the system is
    confident. Defaults mirror the TrueSkill environment in
    ``services/duel/rating.py`` (mu0=1500, sigma0=500) — keep them in sync.
    """

    __tablename__ = 'duel_ratings'
    __table_args__ = (
        UniqueConstraint('user_id', 'mode', name='uq_duel_user_mode'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)

    mode = Column(String(10), nullable=False, default='casual')  # casual | ranked

    # TrueSkill belief
    mu    = Column(Float, default=1500.0, nullable=False)
    sigma = Column(Float, default=500.0, nullable=False)

    # Still played-as-placement until this hits 0 — used only as a leaderboard
    # gate (TrueSkill's sigma already encodes calibration in the math).
    placement_matches_left = Column(Integer, default=10, nullable=False)

    games  = Column(Integer, default=0, nullable=False)
    wins   = Column(Integer, default=0, nullable=False)
    losses = Column(Integer, default=0, nullable=False)

    peak_mu = Column(Float, default=1500.0, nullable=False)
    season_id = Column(Integer, nullable=True)

    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    @property
    def conservative(self) -> float:
        """Leaderboard / division score: mu - 3*sigma, floored at 0."""
        return max(0.0, self.mu - 3.0 * self.sigma)

    def __repr__(self):
        return (f"<DuelRating(user={self.user_id}, mode={self.mode}, "
                f"μ={self.mu:.0f}, σ={self.sigma:.0f}, cons={self.conservative:.0f})>")
