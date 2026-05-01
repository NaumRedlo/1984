"""Migration: per-player BSK map pools (separate pool for each player)."""

from sqlalchemy import text


async def run_bsk_per_player_pool_migration(engine) -> None:
    async with engine.begin() as conn:
        cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(bsk_duels)"))).fetchall()]
        for col, typedef in [
            ("pick_candidates_p1", "TEXT"),    # comma-separated beatmap_ids — player1's pool
            ("pick_candidates_p2", "TEXT"),    # comma-separated beatmap_ids — player2's pool
        ]:
            if col not in cols:
                await conn.execute(text(f"ALTER TABLE bsk_duels ADD COLUMN {col} {typedef}"))
