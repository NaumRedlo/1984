"""Migration: how big a render draws the cursor.

osu! has the same setting and calls it `Cursor size`. It moves the cursor, its
middle and its trail together, because they are one thing.

Null is the size the skin drew them — a cursor size of one — rather than 100
stored, so the default stays the engine's to change.
"""

from sqlalchemy import text

from db.migrations._utils import existing_columns


async def run_render_cursor_migration(engine) -> None:
    async with engine.begin() as conn:
        columns = await existing_columns(conn, "users")
        if "render_cursor" not in columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN render_cursor INTEGER"))
