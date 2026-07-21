"""Render cache: map (score/replay + settings signature + pipeline version) to a
Telegram file_id, so a repeat of the exact same render re-sends instantly without
waking the GPU.
"""

import hashlib
from typing import Optional, Dict

from sqlalchemy import select

from db.database import get_db_session
from db.models.render_cache import RenderCache


# Bump when the render pipeline changes the output bytes (resolution/fps/encoder)
# so stale cached file_ids aren't reused. Cache is also a quick admin-purge target.
RENDER_PIPELINE_VERSION = "1"


_SIG_FIELDS = (
    "skin", "resolution", "bg_dim", "cursor_size",
    "show_pp_counter", "show_scoreboard", "show_key_overlay",
    "show_hit_error_meter", "show_mods", "show_result_screen",
    "show_strain_graph", "show_hit_counter", "show_score", "show_hp_bar",
    "show_seizure_warning", "use_skin_hitsounds", "music_volume", "hitsound_volume",
    "cinema_mode",
)


def _settings_sig(render_settings: Optional[Dict]) -> str:
    """Short signature of the settings that affect the rendered output, so two
    different setups (resolution, HUD toggles, dim, cursor) don't collide in the
    cache."""
    if not render_settings:
        return "def"
    raw = "|".join(f"{k}={render_settings.get(k)}" for k in _SIG_FIELDS)
    # Cache key, not cryptography — usedforsecurity=False keeps the exact same
    # digest (existing cache entries stay valid) while telling FIPS runtimes
    # and scanners this sha1 is fine here.
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()[:12]


def _cache_key(source: str, render_settings: Optional[Dict]) -> str:
    return f"{source}:{_settings_sig(render_settings)}:v{RENDER_PIPELINE_VERSION}"


async def _cache_lookup(key: str) -> Optional[str]:
    async with get_db_session() as session:
        row = (await session.execute(
            select(RenderCache).where(RenderCache.cache_key == key)
        )).scalar_one_or_none()
        return row.file_id if row else None


async def _cache_store(key: str, file_id: str) -> None:
    async with get_db_session() as session:
        existing = (await session.execute(
            select(RenderCache).where(RenderCache.cache_key == key)
        )).scalar_one_or_none()
        if existing:
            existing.file_id = file_id
        else:
            session.add(RenderCache(cache_key=key, file_id=file_id))
        await session.commit()
