from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey, UniqueConstraint
from datetime import datetime, timezone
from db.database import Base


# Conservatism of the leaderboard / division score: ``conservative = mu - K*sigma``.
# This is the single source of truth — the SQL ranking expressions in
# services/leaderboard/service.py and bot/handlers/duel/common.py and the
# profile-card marker all reference it. K=2 keeps a "prove it" buffer while the
# system is unsure (sigma high) but lets a calibrated player's division track
# their real skill (mu); the division thresholds and the pp→mu seed curve are
# both drawn on the mu scale, so a larger K (was 3) buried calibrated players
# 2-3 divisions below their seed.
DUEL_CONSERVATIVE_K = 2.0


class DuelRating(Base):
    """Single-track TrueSkill rating for 1v1 duels.

    One Gaussian skill belief per (user, mode): ``mu`` (mean skill) and
    ``sigma`` (uncertainty). The leaderboard / division layer reads
    ``conservative = mu - DUEL_CONSERVATIVE_K*sigma`` so a player only climbs
    once the system is confident. Defaults mirror the TrueSkill environment in
    ``services/duel/rating.py`` (mu0=2250, sigma0=750) — keep them in sync.
    """

    __tablename__ = 'duel_ratings'
    __table_args__ = (
        UniqueConstraint('user_id', 'mode', name='uq_duel_user_mode'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)

    mode = Column(String(10), nullable=False, default='casual')  # casual | ranked

    # TrueSkill belief
    mu    = Column(Float, default=2250.0, nullable=False)
    sigma = Column(Float, default=750.0, nullable=False)

    # Still played-as-placement until this hits 0 — used only as a leaderboard
    # gate (TrueSkill's sigma already encodes calibration in the math).
    placement_matches_left = Column(Integer, default=10, nullable=False)

    games  = Column(Integer, default=0, nullable=False)
    wins   = Column(Integer, default=0, nullable=False)
    losses = Column(Integer, default=0, nullable=False)

    peak_mu = Column(Float, default=2250.0, nullable=False)
    season_id = Column(Integer, nullable=True)

    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    @property
    def conservative(self) -> float:
        """Leaderboard / division score: mu - K*sigma, floored at 0."""
        return max(0.0, self.mu - DUEL_CONSERVATIVE_K * self.sigma)

    def __repr__(self):
        return (f"<DuelRating(user={self.user_id}, mode={self.mode}, "
                f"μ={self.mu:.0f}, σ={self.sigma:.0f}, cons={self.conservative:.0f})>")
