"""Migration: how loud each half of a render's mix is.

`render_mute` was the whole of the sound settings and it is all-or-nothing.
What somebody actually wants is to hear the play over the song, and that is two
numbers: the map's own track and the hit sounds, each a percentage the way the
game states a volume.

Null is a hundred. An account that has never opened the sound sub-tab gets the
mix every render has always had, and storing that as a null rather than as a
number means the day the natural level changes, they follow it.
"""

from sqlalchemy import text

from db.migrations._utils import existing_columns


async def run_render_levels_migration(engine) -> None:
    async with engine.begin() as conn:
        columns = await existing_columns(conn, "users")
        for name in ("render_music", "render_hitsounds"):
            if name not in columns:
                await conn.execute(
                    text(f"ALTER TABLE users ADD COLUMN {name} INTEGER")
                )
