"""Migration: the render settings that arrived with 4K.

Two of them are preferences with nowhere to live yet — the map's own artwork
behind the play, and the field with no interface on it. The other two are a
counter: renders above 1080p60 cost minutes of a machine rather than seconds, so
they are rationed per person per day, and the ration needs somewhere to be
remembered between renders.

The day is stored as the text of a date rather than as a timestamp. What is asked
of it is only ever "is this still today", and comparing two `YYYY-MM-DD` strings
answers that without a timezone entering the question.
"""

from sqlalchemy import text

from db.migrations._utils import existing_columns


async def run_heavy_render_migration(engine) -> None:
    async with engine.begin() as conn:
        columns = await existing_columns(conn, "users")
        for name, kind in (
            ("render_background", "BOOLEAN"),
            ("render_bare", "BOOLEAN"),
            ("heavy_renders", "INTEGER"),
            ("heavy_renders_on", "VARCHAR(10)"),
        ):
            if name not in columns:
                await conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {kind}"))
