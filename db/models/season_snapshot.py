from sqlalchemy import Column, Integer, String, Float, ForeignKey, UniqueConstraint
from db.database import Base


class SeasonSnapshot(Base):
    __tablename__ = 'season_snapshots'
    __table_args__ = (UniqueConstraint('season_id', 'user_id', name='uq_snapshot_season_user'),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    season_id = Column(Integer, ForeignKey('seasons.id'), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    hps_points = Column(Integer, nullable=False, default=0)
    hps_division = Column(String(30), nullable=False, default='Candidate III')
    duel_conservative = Column(Float, nullable=True)
    duel_division = Column(String(30), nullable=True)

    def __repr__(self):
        return f"<SeasonSnapshot(season={self.season_id}, user={self.user_id}, hps={self.hps_points})>"
