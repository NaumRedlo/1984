"""Migration: add is_test column to bsk_duels."""

from sqlalchemy import text


async def run_bsk_duel_test_migration(engine) -> None:
    async with engine.begin() as conn:
        cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(bsk_duels)"))).fetchall()]
        if "is_test" not in cols:
            await conn.execute(text("ALTER TABLE bsk_duels ADD COLUMN is_test INTEGER NOT NULL DEFAULT 0"))
