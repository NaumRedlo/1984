from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from datetime import datetime, timezone
from db.database import Base


class Duel(Base):
    __tablename__ = 'duels'

    id = Column(Integer, primary_key=True, autoincrement=True)

    player1_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    player2_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)

    best_of = Column(Integer, default=5, nullable=False)

    status = Column(String(20), default="pending", nullable=False)
    # pending → accepted → playing → completed / expired / cancelled

    player1_rounds_won = Column(Integer, default=0, nullable=False)
    player2_rounds_won = Column(Integer, default=0, nullable=False)

    winner_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    chat_id = Column(Integer, nullable=True)
    message_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<Duel(id={self.id}, p1={self.player1_user_id}, p2={self.player2_user_id}, status={self.status})>"
