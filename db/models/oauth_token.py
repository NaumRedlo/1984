from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, LargeBinary
from db.database import Base


class OAuthToken(Base):
    __tablename__ = 'oauth_tokens'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), unique=True, nullable=False, index=True)
    access_token_enc = Column(LargeBinary, nullable=False)
    refresh_token_enc = Column(LargeBinary, nullable=True)
    token_expiry = Column(DateTime, nullable=True)
    scopes = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    def __repr__(self):
        return f"<OAuthToken(user_id={self.user_id}, expiry={self.token_expiry})>"
