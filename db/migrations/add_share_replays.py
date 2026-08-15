"""Migration: add `share_replays` to users.

Consent to send a replay and what the engine made of it to the bot's author.
Persisted rather than kept in memory with the other render settings, because
those are preferences and this is permission: a restart may forget that someone
wanted 60fps, and must not forget whether they agreed to hand over their files.

Defaults to 0 for everyone, including every user who already exists. Consent
that arrives by default is not consent.
"""

from sqlalchemy import text

from db.migrations._utils import existing_columns


async def run_share_replays_migration(engine) -> None:
    async with engine.begin() as conn:
        if "share_replays" not in await existing_columns(conn, "users"):
            await conn.execute(
                text("ALTER TABLE users ADD COLUMN share_replays BOOLEAN DEFAULT 0")
            )
