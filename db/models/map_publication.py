from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, UniqueConstraint

from db.database import Base


class MapPublication(Base):
    __tablename__ = "map_publications"
    __table_args__ = (UniqueConstraint("owner_id", "kind", "local_id", name="uq_map_publication_owner_kind_local"),)

    id = Column(String(32), primary_key=True)
    owner_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    kind = Column(String(16), nullable=False, index=True)
    local_id = Column(String(128), nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    name = Column(String(200), nullable=False)
    content = Column(JSON, nullable=False)
    authors = Column(JSON, nullable=False, default=list)
    updated_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
