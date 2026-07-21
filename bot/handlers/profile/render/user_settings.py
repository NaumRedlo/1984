"""Render settings accessors: fetch-or-create a player's UserRenderSettings row
and flatten it to the plain dict danser_renderer consumes.
"""

from sqlalchemy import select

from db.models.render_settings import UserRenderSettings


async def _get_or_create_settings(session, user_id: int) -> UserRenderSettings:
    """Get user render settings from DB, or return defaults."""
    stmt = select(UserRenderSettings).where(UserRenderSettings.user_id == user_id)
    result = await session.execute(stmt)
    settings = result.scalar_one_or_none()
    if settings:
        return settings
    settings = UserRenderSettings(user_id=user_id)
    session.add(settings)
    await session.commit()
    await session.refresh(settings)
    return settings


def _settings_to_dict(settings: UserRenderSettings) -> dict:
    """Convert DB settings to a plain dict for danser_renderer."""
    return {
        "skin": settings.skin,
        "resolution": settings.resolution,
        "cursor_size": settings.cursor_size,
        "cursor_trail": settings.cursor_trail,
        "show_pp_counter": settings.show_pp_counter,
        "show_scoreboard": settings.show_scoreboard,
        "show_key_overlay": settings.show_key_overlay,
        "show_hit_error_meter": settings.show_hit_error_meter,
        "show_mods": settings.show_mods,
        "show_result_screen": settings.show_result_screen,
        "show_strain_graph": settings.show_strain_graph,
        "show_hit_counter": settings.show_hit_counter,
        "show_score": settings.show_score,
        "show_hp_bar": settings.show_hp_bar,
        "show_seizure_warning": settings.show_seizure_warning,
        "use_skin_hitsounds": settings.use_skin_hitsounds,
        "music_volume": settings.music_volume,
        "hitsound_volume": settings.hitsound_volume,
        "cinema_mode": settings.cinema_mode,
        "bg_dim": settings.bg_dim,
    }
