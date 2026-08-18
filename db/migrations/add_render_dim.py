"""Migration: how far a render darkens the map's own artwork.

osu! calls this `Background dim` and ships it at 70. Ours sat at 82 and could
not be moved, which is one number for two jobs: a render is watched rather than
played, so a bright picture behind a dark skin costs more here — but somebody
rendering to show off a map's art wants the opposite.

Null is the engine's own figure, which is not the same as storing 82: an account
that never chose follows the default rather than pinning today's.
"""

from sqlalchemy import text

from db.migrations._utils import existing_columns


async def run_render_dim_migration(engine) -> None:
    async with engine.begin() as conn:
        columns = await existing_columns(conn, "users")
        if "render_dim" not in columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN render_dim INTEGER"))
