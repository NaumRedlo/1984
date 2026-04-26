"""Migration: BSK per-round map pick phase."""

from sqlalchemy import text


async def run_bsk_pick_phase_migration(engine) -> None:
    async with engine.begin() as conn:
        cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(bsk_duels)"))).fetchall()]
        for col, typedef in [
            ("pick_candidates", "TEXT"),       # comma-separated beatmap_ids
            ("pick_p1",         "INTEGER"),    # beatmap_id chosen by player1, NULL = not picked
            ("pick_p2",         "INTEGER"),    # beatmap_id chosen by player2, NULL = not picked
        ]:
            if col not in cols:
                await conn.execute(text(f"ALTER TABLE bsk_duels ADD COLUMN {col} {typedef}"))
