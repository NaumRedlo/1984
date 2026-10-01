from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from db.database import Base

class LeftMember(Base):
    __tablename__ = "left_members"
    __table_args__ = (
        UniqueConstraint("chat_id", "telegram_id", name="uq_left_members_chat_telegram"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, nullable=False, index=True)
    telegram_id = Column(BigInteger, nullable=False, index=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=True, index=True)
    osu_user_id = Column(Integer, nullable=True)
    osu_username = Column(String(255), nullable=True)
    kept = Column(Text, nullable=False, default="{}")
    left_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    def __repr__(self):
        return f"<LeftMember(chat_id={self.chat_id}, telegram_id={self.telegram_id}, osu='{self.osu_username}')>"
