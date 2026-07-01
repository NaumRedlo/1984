from sqlalchemy import Column, BigInteger, String

from db.database import Base


class UserLanguage(Base):
    """Card-rendering language preference. Global per Telegram user — same
    reasoning as OAuthToken (see db/models/oauth_token.py): a person can be
    registered under several per-chat User rows, but their language choice is
    theirs, not the chat's."""
    __tablename__ = "user_languages"

    telegram_id = Column(BigInteger, primary_key=True)
    language = Column(String(2), nullable=False, default="EN")
