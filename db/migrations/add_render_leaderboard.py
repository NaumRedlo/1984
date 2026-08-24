"""Migration: whether a render draws the chat's scoreboard down its left.

It is the one part of the picture that is about other people, and somebody
rendering a play to show the play may not want the column of names beside it.

Null is on — every render before this had one whenever the chat could supply
one, and a setting that arrives switched off would take a feature away from
everybody who never opens the menu.
"""

from sqlalchemy import text

from db.migrations._utils import existing_columns


async def run_render_leaderboard_migration(engine) -> None:
    async with engine.begin() as conn:
        columns = await existing_columns(conn, "users")
        if "render_leaderboard" not in columns:
            await conn.execute(
                text("ALTER TABLE users ADD COLUMN render_leaderboard BOOLEAN")
            )
