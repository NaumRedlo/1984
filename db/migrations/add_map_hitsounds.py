"""Migration: whether a render plays the map's own hit sounds.

osu!'s `Ignore beatmap hitsounds`, the other way up. It is a setting there and
it is a setting here for the same reason: the two answers sound entirely
different on a hitsounded map, and which one somebody wants is about why they
are watching rather than about which is correct.

Null is the default, and the default is on, the way the game ships it: a
hitsounded map is most of what a hitsounded map sounds like, and a skin usually
has nothing to put where the map's custom indices go.
"""

from sqlalchemy import text

from db.migrations._utils import existing_columns


async def run_map_hitsounds_migration(engine) -> None:
    async with engine.begin() as conn:
        columns = await existing_columns(conn, "users")
        if "render_map_hitsounds" not in columns:
            await conn.execute(
                text("ALTER TABLE users ADD COLUMN render_map_hitsounds BOOLEAN")
            )
