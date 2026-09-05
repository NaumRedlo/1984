from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, Integer, String

from db.database import Base

class RenderWorkerToken(Base):

    __tablename__ = "render_worker_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)

    digest = Column(String(64), unique=True, nullable=False, index=True)

    issued_to = Column(BigInteger, nullable=True, index=True)
    issued_name = Column(String(128), nullable=True)

    worker = Column(String(128), nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    last_seen = Column(DateTime, nullable=True)

    revoked_at = Column(DateTime, nullable=True)
