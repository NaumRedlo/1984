from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Index
from datetime import datetime, timezone
from db.database import Base


class BskDuelRound(Base):
    __tablename__ = 'bsk_duel_rounds'
    __table_args__ = (
        Index('ix_bsk_duel_rounds_duel_id', 'duel_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    duel_id = Column(Integer, ForeignKey('bsk_duels.id'), nullable=False)

    round_number = Column(Integer, nullable=False)

    # Map info
    beatmap_id = Column(Integer, nullable=True)
    beatmapset_id = Column(Integer, nullable=True)
    beatmap_title = Column(String(255), nullable=True)
    star_rating = Column(Float, default=0.0, nullable=False)

    # Map skill weights (from composite or ML)
    w_aim   = Column(Float, default=0.25, nullable=False)
    w_speed = Column(Float, default=0.25, nullable=False)
    w_acc   = Column(Float, default=0.25, nullable=False)
    w_cons  = Column(Float, default=0.25, nullable=False)

    # Player scores
    player1_score     = Column(Integer, nullable=True)
    player1_accuracy  = Column(Float, nullable=True)
    player1_combo     = Column(Integer, nullable=True)
    player1_misses    = Column(Integer, nullable=True)
    player1_pp        = Column(Float, nullable=True)
    player1_composite = Column(Float, nullable=True)  # BSK composite score
    player1_submitted_at = Column(DateTime, nullable=True)

    player2_score     = Column(Integer, nullable=True)
    player2_accuracy  = Column(Float, nullable=True)
    player2_combo     = Column(Integer, nullable=True)
    player2_misses    = Column(Integer, nullable=True)
    player2_pp        = Column(Float, nullable=True)
    player2_composite = Column(Float, nullable=True)
    player2_submitted_at = Column(DateTime, nullable=True)

    winner_player = Column(Integer, nullable=True)  # 1 or 2, None if not finished

    status = Column(String(20), nullable=False, default='waiting')
    # waiting → playing → completed | forfeit

    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at = Column(DateTime, nullable=True)
    forfeit_at = Column(DateTime, nullable=True)  # deadline for players to submit

    def __repr__(self):
        return f"<BskDuelRound(duel={self.duel_id}, round={self.round_number}, status={self.status})>"
