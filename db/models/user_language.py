from sqlalchemy import Column, BigInteger, String

from db.database import Base

class UserLanguage(Base):
    __tablename__ = "user_languages"

    telegram_id = Column(BigInteger, primary_key=True)
    language = Column(String(2), nullable=False, default="EN")
