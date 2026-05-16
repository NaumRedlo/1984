from sqlalchemy import Column, String

from db.database import Base


class BotSettings(Base):
    __tablename__ = "bot_settings"

    key = Column(String, primary_key=True)
    value = Column(String, nullable=True)
