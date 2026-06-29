from sqlalchemy import Column, Integer, String, Float, Boolean, ForeignKey
from db.database import Base


class UserRenderSettings(Base):
    __tablename__ = 'user_render_settings'

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey('users.id'), unique=True, nullable=False, index=True)

    skin = Column(String(255), default='default', nullable=False)
    # 1080p default for the GPU renderer; the /settings menu lets users pick lower.
    resolution = Column(String(20), default='1920x1080', nullable=False)

    # Cursor
    cursor_size = Column(Float, default=1.0, nullable=False)
    cursor_trail = Column(Boolean, default=True, nullable=False)

    # UI elements
    show_pp_counter = Column(Boolean, default=True, nullable=False)
    show_scoreboard = Column(Boolean, default=False, nullable=False)
    show_key_overlay = Column(Boolean, default=True, nullable=False)
    show_hit_error_meter = Column(Boolean, default=True, nullable=False)
    show_mods = Column(Boolean, default=True, nullable=False)
    show_result_screen = Column(Boolean, default=True, nullable=False)

    # Background
    bg_dim = Column(Integer, default=80, nullable=False)
