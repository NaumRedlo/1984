from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Index
from datetime import datetime, timezone
from db.database import Base


class Duel(Base):
    """A 1v1 duel: an auto-built map pool played best-of-N over an osu! IRC
    multiplayer room.  Round wins are tracked directly (hardcore scoring: a
    failed map scores no point); first to ``win_target`` takes the duel.
    """

    __tablename__ = 'duels'
    __table_args__ = (
        Index('ix_duels_status', 'status'),
        Index('ix_duels_players', 'player1_user_id', 'player2_user_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    player1_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    player2_user_id = Column(Integer, ForeignKey('users.id'), nullable=False)

    mode = Column(String(10), nullable=False, default='casual')  # casual | ranked

    status = Column(String(20), nullable=False, default='pending')
    # pending → accepted → round_active → completed | cancelled | expired

    chat_id = Column(Integer, nullable=True)
    message_id = Column(Integer, nullable=True)          # main challenge message
    message_thread_id = Column(Integer, nullable=True)   # forum topic; NULL = General

    # osu! multiplayer match linked to this duel.
    osu_match_id = Column(Integer, nullable=True)

    # Auto-built pool: comma-separated beatmap_ids played in order.
    pool_beatmap_ids = Column(String(512), nullable=True)

    # Best-of bookkeeping.
    total_rounds = Column(Integer, default=0, nullable=False)   # pool size (5 / 10)
    win_target = Column(Integer, default=0, nullable=False)     # rounds needed (3 / 6)
    current_round = Column(Integer, default=0, nullable=False)
    player1_rounds_won = Column(Integer, default=0, nullable=False)
    player2_rounds_won = Column(Integer, default=0, nullable=False)

    winner_user_id = Column(Integer, ForeignKey('users.id'), nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    accepted_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=True)   # pending accept timeout

    def __repr__(self):
        return (f"<Duel(id={self.id}, p1={self.player1_user_id}, "
                f"p2={self.player2_user_id}, status={self.status}, "
                f"{self.player1_rounds_won}:{self.player2_rounds_won})>")
