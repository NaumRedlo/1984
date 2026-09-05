from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime

from db.database import Base

class DmActiveTenant(Base):

    __tablename__ = "dm_active_tenant"

    telegram_id = Column(BigInteger, primary_key=True)
    chat_id = Column(BigInteger, nullable=False)
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self):
        return f"<DmActiveTenant(tg={self.telegram_id}, chat_id={self.chat_id})>"
