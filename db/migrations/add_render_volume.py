"""Migration: how loud a render is, over the two halves of its mix.

The music and the hit sounds already have levels of their own; this is the
fader over both, for somebody who wants the whole thing quieter or louder
without changing the balance between them.

Null is the natural level rather than 100 stored, so the default stays the
engine's to change.
"""

from sqlalchemy import text

from db.migrations._utils import existing_columns


async def run_render_volume_migration(engine) -> None:
    async with engine.begin() as conn:
        columns = await existing_columns(conn, "users")
        if "render_volume" not in columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN render_volume INTEGER"))
