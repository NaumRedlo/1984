"""Migration: add last_seen_at to users table."""

from sqlalchemy import text


async def run_last_seen_migration(engine) -> None:
    async with engine.begin() as conn:
        cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(users)"))).fetchall()]
        if "last_seen_at" not in cols:
            await conn.execute(text("ALTER TABLE users ADD COLUMN last_seen_at DATETIME"))
