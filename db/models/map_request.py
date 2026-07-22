from datetime import datetime, timezone

from sqlalchemy import Column, Integer, BigInteger, Float, String, DateTime, ForeignKey, Index

from db.database import Base


# Lifecycle: pending -> accepted|declined ; accepted -> completed|cancelled.
STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_DECLINED = "declined"
STATUS_CANCELLED = "cancelled"
STATUS_COMPLETED = "completed"

# Statuses that still count as "live" for a target's task list / uniqueness guard.
OPEN_STATUSES = (STATUS_PENDING, STATUS_ACCEPTED)


class MapRequest(Base):
    """One player challenges another to pass a specific beatmap under conditions.

    Scoped to a tenant (group): sender and target are both User rows in the same
    chat_id. Progress isn't stored here — it's derived on demand from
    UserMapAttempt for (target_user_id, beatmap_id). Completion is detected
    automatically when the target's synced plays satisfy `conditions`.
    """
    __tablename__ = "map_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_chat_id = Column(BigInteger, nullable=False, index=True)
    sender_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    target_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    beatmap_id = Column(Integer, nullable=False, index=True)
    beatmapset_id = Column(Integer, nullable=True)
    # Snapshot for display without a re-fetch.
    artist = Column(String(255), nullable=True)
    title = Column(String(255), nullable=True)
    version = Column(String(255), nullable=True)
    star_rating = Column(Float, nullable=True)
    bpm = Column(Float, nullable=True)
    length = Column(Integer, nullable=True)          # seconds (map total_length)
    map_max_combo = Column(Integer, nullable=True)   # map's max combo (for the card)
    mapper_id = Column(Integer, nullable=True)       # beatmapset host (for the mapper avatar)

    # JSON: {"pass": bool, "min_accuracy": float|null, "require_fc": bool,
    #        "min_combo": int|null, "mods": str|null, "min_rank": str|null}
    conditions = Column(String(1024), nullable=False)

    status = Column(String(20), nullable=False, default=STATUS_PENDING, index=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    responded_at = Column(DateTime, nullable=True)     # accept/decline time
    completed_at = Column(DateTime, nullable=True)
    completing_score_id = Column(BigInteger, nullable=True)

    __table_args__ = (
        Index("ix_map_requests_target_status", "target_user_id", "status"),
        Index("ix_map_requests_tenant_status", "tenant_chat_id", "status"),
    )

    def __repr__(self):
        return (f"<MapRequest(id={self.id}, sender={self.sender_user_id}, "
                f"target={self.target_user_id}, beatmap={self.beatmap_id}, status={self.status})>")
