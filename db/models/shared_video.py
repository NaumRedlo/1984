from datetime import datetime, timezone

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from db.database import Base

class SharedVideo(Base):
    __tablename__ = "shared_videos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner = Column(BigInteger, nullable=False, index=True)
    owner_player_id = Column(Integer, nullable=True, index=True)

    kind = Column(String(16), nullable=True)
    file_id = Column(String(255), nullable=False)
    file_unique_id = Column(String(64), nullable=True, index=True)
    thumb_id = Column(String(255), nullable=True)
    size = Column(BigInteger, nullable=True)
    duration = Column(Integer, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)

    player = Column(String(255), nullable=True)
    song = Column(String(512), nullable=True)
    version = Column(String(255), nullable=True)
    mods = Column(String(64), nullable=True)
    map_hash = Column(String(32), nullable=True)
    replay_hash = Column(String(32), nullable=True)
    replay_sha256 = Column(String(64), nullable=True)
    settings = Column(Text, nullable=True)
    storage_hash = Column(String(64), nullable=True)
    stored_until = Column(DateTime, nullable=True, index=True)
    skin_name = Column(String(128), nullable=True)
    skin_hash = Column(String(64), nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

class VideoDelivery(Base):
    __tablename__ = "video_deliveries"
    __table_args__ = (
        UniqueConstraint("video_id", "recipient_id", name="uq_video_deliveries_video_recipient"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    video_id = Column(Integer, ForeignKey("shared_videos.id"), nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    recipient_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    sent_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    seen_at = Column(DateTime, nullable=True)
