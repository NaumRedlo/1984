from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, UniqueConstraint
from db.database import Base

class UserTitleProgress(Base):
    __tablename__ = 'user_title_progress'

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey('players.id'), nullable=False, index=True)
    title_code = Column(String(50), nullable=False)
    current_value = Column(Integer, default=0, nullable=False)
    unlocked = Column(Boolean, default=False, nullable=False)
    unlocked_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint('player_id', 'title_code', name='uq_player_title'),
    )

    def __repr__(self):
        return f"<UserTitleProgress(player={self.player_id}, title='{self.title_code}', value={self.current_value}, unlocked={self.unlocked})>"
