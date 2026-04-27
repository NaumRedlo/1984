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
    ar       = Column(Float, nullable=True)
    od       = Column(Float, nullable=True)
    cs       = Column(Float, nullable=True)
    hp_drain = Column(Float, nullable=True)

    # Skill weights (manual or ML-derived)
    w_aim   = Column(Float, default=0.25, nullable=False)
    w_speed = Column(Float, default=0.25, nullable=False)
    w_acc   = Column(Float, default=0.25, nullable=False)
    w_cons  = Column(Float, default=0.25, nullable=False)

    # Map type tag for adaptive pressure
    map_type = Column(String(20), nullable=True)  # aim | speed | acc | cons

    # osu! SR algorithm attributes (from API /beatmaps/{id}/attributes)
    api_aim_diff       = Column(Float, nullable=True)   # aim difficulty rating
    api_speed_diff     = Column(Float, nullable=True)   # speed difficulty rating
    api_slider_factor  = Column(Float, nullable=True)   # 0.0–1.0 (1 = pure circles)
    api_speed_note_count = Column(Float, nullable=True)  # number of speed notes

    # Parsed .osu pattern features (stored so recalc is instant)
    f_burst        = Column(Float, nullable=True)  # burst_density
    f_stream       = Column(Float, nullable=True)  # full_stream_density
    f_death_stream = Column(Float, nullable=True)  # death_stream_density
    f_jump_vel     = Column(Float, nullable=True)  # avg_jump_velocity
    f_back_forth   = Column(Float, nullable=True)  # back_forth_ratio
    f_angle_var    = Column(Float, nullable=True)  # angle_variance
    f_sv_var       = Column(Float, nullable=True)  # sv_variance
    f_density_var  = Column(Float, nullable=True)  # density_variance
    f_rhythm_complexity = Column(Float, nullable=True)
    f_slider_density    = Column(Float, nullable=True)
    f_jump_density      = Column(Float, nullable=True)
    f_note_count        = Column(Integer, nullable=True)
    f_duration          = Column(Integer, nullable=True)

    enabled = Column(Boolean, default=True, nullable=False)

    def __repr__(self):
        return f"<BskMapPool(id={self.beatmap_id}, '{self.title}', {self.star_rating}★)>"
