"""Migration: how big a render draws the hit-error meter.

The first thing anybody asked for once the renderer was shown around. osu!
has the same knob — `Score meter size` — and it is a preference of whoever is
watching rather than anything a replay records, so there is no value here that
a play can be said to have.

Null is the engine's own figure, which is not the same as storing 100: an
account that never chose follows the default rather than pinning today's.
"""

from sqlalchemy import text

from db.migrations._utils import existing_columns


async def run_render_meter_migration(engine) -> None:
    async with engine.begin() as conn:
        columns = await existing_columns(conn, "users")
        if "render_meter" not in columns:
            await conn.execute(text("ALTER TABLE users ADD COLUMN render_meter INTEGER"))
