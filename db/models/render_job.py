from sqlalchemy import JSON, BigInteger, Boolean, Column, Float, Index, Integer, String, Text

from db.database import Base


class RenderJob(Base):
    __tablename__ = "render_jobs"

    seq = Column(Integer, primary_key=True, autoincrement=True)
    id = Column(String(16), nullable=False, unique=True)
    replay_path = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    beatmap_md5 = Column(String(64), nullable=False)
    beatmapset_id = Column(Integer, nullable=True)
    skin = Column(JSON, nullable=True)
    requester = Column(BigInteger, nullable=False, default=0)
    requester_player = Column(Integer, nullable=True)
    chat_id = Column(BigInteger, nullable=False, default=0)
    created = Column(Float, nullable=False)
    state = Column(String(16), nullable=False, default="waiting")
    worker = Column(String(128), nullable=True)
    worker_name = Column(String(128), nullable=False, default="")
    lease_until = Column(Float, nullable=False, default=0.0)
    attempts = Column(Integer, nullable=False, default=0)
    progress = Column(JSON, nullable=True)
    payload = Column(JSON, nullable=True)
    reason = Column(Text, nullable=False, default="")
    video_id = Column(Integer, nullable=True)
    settings = Column(JSON, nullable=True)
    in_app = Column(Boolean, nullable=False, default=False)
    video_meta = Column(JSON, nullable=True)
    ruleset = Column(Integer, nullable=False, default=0)
    leaderboard = Column(JSON, nullable=True)
    troubles = Column(JSON, nullable=True)
    followed = Column(Boolean, nullable=False, default=False)
    told = Column(JSON, nullable=True)

    __table_args__ = (Index("ix_render_jobs_state", "state", "created"),)
