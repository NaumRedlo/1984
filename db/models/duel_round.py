from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Index
from datetime import datetime, timezone
from db.database import Base


class DuelRound(Base):
    """One map of a duel.  Hardcore scoring: a player who fails the map gets no
    point.  ``winner_player`` is 1/2, or NULL when both failed (void round) or
    the round was forfeited.
    """

    __tablename__ = 'duel_rounds'
    __table_args__ = (
        Index('ix_duel_rounds_duel_id', 'duel_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    duel_id = Column(Integer, ForeignKey('duels.id'), nullable=False)

    round_number = Column(Integer, nullable=False)

    # Map info
    beatmap_id = Column(Integer, nullable=True)
    beatmapset_id = Column(Integer, nullable=True)
    beatmap_title = Column(String(255), nullable=True)
    star_rating = Column(Float, default=0.0, nullable=False)

    # Player results (from the linked osu! match)
    player1_score    = Column(Integer, nullable=True)
    player1_accuracy = Column(Float, nullable=True)
    player1_combo    = Column(Integer, nullable=True)
    player1_misses   = Column(Integer, nullable=True)
    player1_passed   = Column(Boolean, nullable=True)

    player2_score    = Column(Integer, nullable=True)
    player2_accuracy = Column(Float, nullable=True)
    player2_combo    = Column(Integer, nullable=True)
    player2_misses   = Column(Integer, nullable=True)
    player2_passed   = Column(Boolean, nullable=True)

    winner_player = Column(Integer, nullable=True)  # 1 / 2 / None (void or forfeit)

    status = Column(String(20), nullable=False, default='waiting')
    # waiting → playing → completed | void | forfeit

    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at = Column(DateTime, nullable=True)
    forfeit_at = Column(DateTime, nullable=True)  # deadline to submit a score

    def __repr__(self):
        return (f"<DuelRound(duel={self.duel_id}, round={self.round_number}, "
                f"status={self.status}, winner=p{self.winner_player})>")
