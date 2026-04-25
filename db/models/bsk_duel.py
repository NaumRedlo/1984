from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Index, Boolean
from datetime import datetime, timezone
from db.database import Base


class BskDuel(Base):
    __tablename__ = 'bsk_duels'
    __table_args__ = (
        Index('ix_bsk_duels_status', 'status'),
        Index('ix_bsk_duels_players', 'player1_user_id', 'player2_user_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    player1_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    player2_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)

    mode = Column(String(10), nullable=False, default='casual')  # casual | ranked
    is_test = Column(Boolean, nullable=False, default=False)

    status = Column(String(20), nullable=False, default='pending')
    # pending → accepted → round_active → completed | cancelled | expired

    chat_id = Column(Integer, nullable=True)
    message_id = Column(Integer, nullable=True)  # main duel message

    # Score Race: cumulative composite scores
    player1_total_score = Column(Float, default=0.0, nullable=False)
    player2_total_score = Column(Float, default=0.0, nullable=False)

    winner_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    current_round = Column(Integer, default=0, nullable=False)
    total_rounds = Column(Integer, default=5, nullable=False)

    target_score = Column(Integer, default=1_000_000, nullable=False)
    version = Column(Integer, default=2, nullable=False)

    # Pause state
    pause_votes = Column(Integer, default=0, nullable=False)  # bitmask: 1=p1, 2=p2
    paused_at = Column(DateTime, nullable=True)

    # Adaptive pressure state
    current_star_rating = Column(Float, default=0.0, nullable=False)
    pressure_offset = Column(Float, default=0.0, nullable=False)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    accepted_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)  # for pending accept timeout

    def __repr__(self):
        return f"<BskDuel(id={self.id}, p1={self.player1_user_id}, p2={self.player2_user_id}, status={self.status}, test={self.is_test})>"
