"""Migration: remember a person's render settings.

They lived in memory beside the pending renders, which was fine while the only
cost of a restart was re-picking a resolution — but a skin is chosen from a list
somebody had to send the bot first, and losing that is losing work rather than a
preference. All four move together so the settings screen has one place to read
from and one to write to.
"""

from sqlalchemy import text

from db.migrations._utils import existing_columns


async def run_render_settings_migration(engine) -> None:
    async with engine.begin() as conn:
        columns = await existing_columns(conn, "users")
        for name, kind in (
            ("render_size", "VARCHAR(16)"),
            ("render_fps", "INTEGER"),
            ("render_mute", "BOOLEAN"),
            ("render_skin", "VARCHAR(64)"),
        ):
            if name not in columns:
                await conn.execute(text(f"ALTER TABLE users ADD COLUMN {name} {kind}"))
