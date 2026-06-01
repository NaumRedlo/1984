from sqlalchemy import Column, Integer, String, Float, Boolean
from db.database import Base


class DuelMapPool(Base):
    __tablename__ = 'duel_map_pool'

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

    # Skill weights — share form, derived from stars via softmax (UI/legacy use)
    w_aim   = Column(Float, default=0.25, nullable=False)
    w_speed = Column(Float, default=0.25, nullable=False)
    w_acc   = Column(Float, default=0.25, nullable=False)
    w_cons  = Column(Float, default=0.25, nullable=False)

    # Independent skill stars [0..10] — primary classification source
    aim_stars   = Column(Float, nullable=True)
    speed_stars = Column(Float, nullable=True)
    acc_stars   = Column(Float, nullable=True)
    cons_stars  = Column(Float, nullable=True)

    # Map type tag = argmax(*_stars) when stars present, else argmax(w_*)
    map_type = Column(String(20), nullable=True)  # aim | speed | acc | cons

    # osu! SR algorithm attributes (from API /beatmaps/{id}/attributes)
    api_aim_diff       = Column(Float, nullable=True)   # aim difficulty rating
    api_speed_diff     = Column(Float, nullable=True)   # speed difficulty rating
    api_slider_factor  = Column(Float, nullable=True)   # 0.0–1.0 (1 = pure circles)
    api_speed_note_count = Column(Float, nullable=True)  # number of speed notes

    # ── Parsed .osu pattern features (stored so recalc is instant) ──
    # Aim signals
    f_jump_density      = Column(Float, nullable=True)
    f_jump_vel          = Column(Float, nullable=True)
    f_back_forth        = Column(Float, nullable=True)
    f_angle_var         = Column(Float, nullable=True)
    f_flow_break        = Column(Float, nullable=True)  # NEW

    # Speed signals
    f_burst             = Column(Float, nullable=True)
    f_stream            = Column(Float, nullable=True)
    f_death_stream      = Column(Float, nullable=True)
    f_bpm_rel_speed     = Column(Float, nullable=True)  # NEW

    # Accuracy signals
    f_subdiv_entropy     = Column(Float, nullable=True)  # NEW
    f_polyrhythm_density = Column(Float, nullable=True)  # NEW
    f_off_beat_ratio     = Column(Float, nullable=True)  # NEW
    f_jack_density       = Column(Float, nullable=True)  # NEW
    f_slider_tail_demand = Column(Float, nullable=True)  # NEW
    f_od_demand          = Column(Float, nullable=True)  # NEW
    f_sv_var             = Column(Float, nullable=True)
    f_slider_density     = Column(Float, nullable=True)

    # Consistency signals
    f_density_var      = Column(Float, nullable=True)
    f_intensity_floor  = Column(Float, nullable=True)  # NEW
    f_pattern_repeat   = Column(Float, nullable=True)  # NEW

    # General / shared
    f_rhythm_complexity = Column(Float, nullable=True)  # CV of intervals — kept for ML
    f_note_count        = Column(Integer, nullable=True)
    f_duration          = Column(Integer, nullable=True)

    enabled = Column(Boolean, default=True, nullable=False)

    def __repr__(self):
        return f"<DuelMapPool(id={self.beatmap_id}, '{self.title}', {self.star_rating}★)>"
