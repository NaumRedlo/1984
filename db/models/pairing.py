from sqlalchemy import BigInteger, Column, DateTime, Integer, String

from db.database import Base


class PairingRow(Base):
    __tablename__ = "pairings"

    code = Column(String(16), primary_key=True)
    name = Column(String(255), nullable=False)
    os = Column(String(64), nullable=False, default="")
    cores = Column(Integer, nullable=False, default=0)
    build = Column(String(64), nullable=False, default="")
    expires_at = Column(DateTime, nullable=False, index=True)
    linked_to = Column(BigInteger, nullable=True)
    linked_name = Column(String(255), nullable=False, default="")
    linked_player = Column(Integer, nullable=True)
    wants_telegram_for = Column(Integer, nullable=True)
    osu_state = Column(String(64), nullable=True, unique=True)
