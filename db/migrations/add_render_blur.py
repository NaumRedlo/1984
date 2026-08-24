"""Migration: how hard a render blurs the map's own artwork.

It was blurred and could not be un-blurred, which is right for a render watched
to see a play and wrong for one made to show a map. osu! has the same setting.

Null is the engine's own figure rather than 100 stored, so the default stays
the engine's to change.
"""

from sqlalchemy import text

from db.migrations._utils import existing_columns


async def run_render_blur_migration(engine) -> None:
    async with engine.begin() as conn:
        columns = await existing_columns(conn, "users")
        if "render_blur" not in columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN render_blur INTEGER"))
