from sqlalchemy import Column, Integer, String, Text, DateTime, UniqueConstraint
from db.database import Base
from utils.timeutils import utcnow


class UserRender(Base):
    """A player's personal library of replays they've rendered. Stores the
    Telegram file_id of the finished video plus a metadata snapshot (map / score
    details) so the /settings "Мои рендеры" picker can show what each entry is and
    re-send it instantly — no GPU, no danser. Capped per user (oldest pruned).

    Deduped per (user_id, ref): re-rendering the same score updates the row (newest
    file_id, bumped created_at) instead of adding a duplicate. ref is
    "score:<id>" or "osr:<sha1>"."""

    __tablename__ = 'user_renders'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    ref = Column(String(80), nullable=False)
    file_id = Column(String(512), nullable=False)
    label = Column(String(255), nullable=False, default="")
    meta = Column(Text, nullable=True)            # JSON snapshot for the detail view
    created_at = Column(DateTime, default=utcnow, nullable=False)

    __table_args__ = (UniqueConstraint('user_id', 'ref', name='uq_user_render'),)
