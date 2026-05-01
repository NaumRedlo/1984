"""Migration: add message_thread_id column to bsk_duels for forum-topic routing."""

from sqlalchemy import text


async def run_bsk_duel_thread_id_migration(engine) -> None:
    async with engine.begin() as conn:
        cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(bsk_duels)"))).fetchall()]
        if "message_thread_id" not in cols:
            await conn.execute(text("ALTER TABLE bsk_duels ADD COLUMN message_thread_id INTEGER"))
