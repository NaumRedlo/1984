"""Migration: BSK alternating pick turn + played tracking."""

from sqlalchemy import text


async def run_bsk_pool_turn_migration(engine) -> None:
    async with engine.begin() as conn:
        cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(bsk_duels)"))).fetchall()]
        for col, typedef in [
            ("pick_turn",   "INTEGER"),   # 1 or 2: whose turn to pick
            ("pick_played", "TEXT"),       # comma-separated beatmap_ids already played from pool
        ]:
            if col not in cols:
                await conn.execute(text(f"ALTER TABLE bsk_duels ADD COLUMN {col} {typedef}"))
