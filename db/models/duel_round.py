from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from datetime import datetime, timezone
from db.database import Base


class DuelRound(Base):
    __tablename__ = 'duel_rounds'

    id = Column(Integer, primary_key=True, autoincrement=True)

    duel_id = Column(Integer, ForeignKey('duels.id'), nullable=False)
    round_number = Column(Integer, nullable=False)

    beatmap_id = Column(Integer, nullable=False)
    beatmap_title = Column(String(255), nullable=True)
    star_rating = Column(Float, nullable=True)

    player1_score = Column(Integer, nullable=True)
    player1_accuracy = Column(Float, nullable=True)
    player1_combo = Column(Integer, nullable=True)

    player2_score = Column(Integer, nullable=True)
    player2_accuracy = Column(Float, nullable=True)
    player2_combo = Column(Integer, nullable=True)

    winner_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    completed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<DuelRound(id={self.id}, duel={self.duel_id}, round={self.round_number})>"
