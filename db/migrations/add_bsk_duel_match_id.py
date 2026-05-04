"""Migration: add osu_match_id column to bsk_duels.

Stores the osu! multiplayer match ID linked to a duel. Score collection reads
match events from /api/v2/matches/{id}, so failed passes count and players no
longer need NoFail.
"""

from sqlalchemy import text


async def run_bsk_duel_match_id_migration(engine) -> None:
    async with engine.begin() as conn:
        cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(bsk_duels)"))).fetchall()]
        if "osu_match_id" not in cols:
            await conn.execute(text("ALTER TABLE bsk_duels ADD COLUMN osu_match_id INTEGER"))
