from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, Integer, String

from db.database import Base


class OAuthPending(Base):
    """A link sent to someone that osu! has not called back yet. Kept in the database so a
    restart of the bot does not turn a link that was just sent into "link expired"."""

    __tablename__ = "oauth_pending"

    state = Column(String(64), primary_key=True)
    telegram_id = Column(BigInteger, nullable=False, index=True)
    issued_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    chat_id = Column(BigInteger, nullable=True)
    message_id = Column(Integer, nullable=True)
