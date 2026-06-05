from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, BigInteger, LargeBinary
from db.database import Base


class OAuthToken(Base):
    __tablename__ = 'oauth_tokens'

    id = Column(Integer, primary_key=True, autoincrement=True)
    # OAuth identity is global per Telegram user, independent of which group(s)
    # they registered in — keyed by telegram_id, not a per-tenant users.id.
    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    access_token_enc = Column(LargeBinary, nullable=False)
    refresh_token_enc = Column(LargeBinary, nullable=True)
    token_expiry = Column(DateTime, nullable=True)
    scopes = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    def __repr__(self):
        return f"<OAuthToken(telegram_id={self.telegram_id}, expiry={self.token_expiry})>"
