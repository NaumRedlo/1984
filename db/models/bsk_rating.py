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

    # 4 skill components, each starts at 250 → global mu = 1000
    mu_aim   = Column(Float, default=250.0, nullable=False)
    mu_speed = Column(Float, default=250.0, nullable=False)
    mu_acc   = Column(Float, default=250.0, nullable=False)
    mu_cons  = Column(Float, default=250.0, nullable=False)

    # uncertainty per component
    sigma_aim   = Column(Float, default=100.0, nullable=False)
    sigma_speed = Column(Float, default=100.0, nullable=False)
    sigma_acc   = Column(Float, default=100.0, nullable=False)
    sigma_cons  = Column(Float, default=100.0, nullable=False)

    placement_matches_left = Column(Integer, default=10, nullable=False)

    wins   = Column(Integer, default=0, nullable=False)
    losses = Column(Integer, default=0, nullable=False)

    peak_mu = Column(Float, default=1000.0, nullable=False)

    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    @property
    def mu_global(self) -> float:
        return (
            0.30 * self.mu_aim +
            0.30 * self.mu_speed +
            0.25 * self.mu_acc +
            0.15 * self.mu_cons
        )

    @property
    def conservative(self) -> float:
        """Leaderboard score: mu_global - 3*avg_sigma."""
        avg_sigma = (self.sigma_aim + self.sigma_speed + self.sigma_acc + self.sigma_cons) / 4
        return max(0.0, self.mu_global - 3 * avg_sigma)

    def __repr__(self):
        return f"<BskRating(user={self.user_id}, mode={self.mode}, μ={self.mu_global:.1f})>"
