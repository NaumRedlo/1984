from sqlalchemy import Column, Integer, String, Float, Boolean
from db.database import Base


class BskMapPool(Base):
    __tablename__ = 'bsk_map_pool'

    id = Column(Integer, primary_key=True, autoincrement=True)
    beatmap_id = Column(Integer, unique=True, nullable=False, index=True)
    beatmapset_id = Column(Integer, nullable=False)

    title = Column(String(255), nullable=False)
    artist = Column(String(255), nullable=False)
    version = Column(String(255), nullable=False)  # difficulty name
    creator = Column(String(255), nullable=True)

    star_rating = Column(Float, nullable=False)
    bpm = Column(Float, nullable=True)
    length = Column(Integer, nullable=True)  # seconds
    ar = Column(Float, nullable=True)
    od = Column(Float, nullable=True)
    cs = Column(Float, nullable=True)

    # Skill weights (manual or ML-derived)
    w_aim   = Column(Float, default=0.25, nullable=False)
    w_speed = Column(Float, default=0.25, nullable=False)
    w_acc   = Column(Float, default=0.25, nullable=False)
    w_cons  = Column(Float, default=0.25, nullable=False)

    # Map type tag for adaptive pressure
    map_type = Column(String(20), nullable=True)  # aim | speed | acc | balanced

    enabled = Column(Boolean, default=True, nullable=False)

    def __repr__(self):
        return f"<BskMapPool(id={self.beatmap_id}, '{self.title}', {self.star_rating}★)>"
