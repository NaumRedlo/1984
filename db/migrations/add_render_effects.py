"""Migration: which optional movements somebody's renders have on.

One column rather than five. Each of them is a yes-or-no — a slider body that
grows out of its head, one that retreats behind the ball, a cursor that swells
under a click, the smear behind it, the flash a struck note throws — but they
arrive together and are read together, and every one of them added since would
have been another `ALTER TABLE` and another null to interpret. Stored as the
comma-separated list the engine's `--effects` already takes, so nothing between
here and the command line has to know what the names are.

Null is not the empty string. Null is somebody who has never opened the
sub-tabs, and they get the defaults; the empty string is somebody who went in
and switched all five off, and they are obeyed.
"""

from sqlalchemy import text

from db.migrations._utils import existing_columns


async def run_render_effects_migration(engine) -> None:
    async with engine.begin() as conn:
        columns = await existing_columns(conn, "users")
        if "render_effects" not in columns:
            await conn.execute(
                text("ALTER TABLE users ADD COLUMN render_effects VARCHAR(128)")
            )
