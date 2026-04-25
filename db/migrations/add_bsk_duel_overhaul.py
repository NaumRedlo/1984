"""Migration: BSK duel overhaul — race to 1M, per-round ratings, points."""

from sqlalchemy import text


async def run_bsk_duel_overhaul_migration(engine) -> None:
    async with engine.begin() as conn:
        # --- bsk_duels ---
        cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(bsk_duels)"))).fetchall()]
        if "target_score" not in cols:
            await conn.execute(text("ALTER TABLE bsk_duels ADD COLUMN target_score INTEGER NOT NULL DEFAULT 1000000"))
        if "version" not in cols:
            await conn.execute(text("ALTER TABLE bsk_duels ADD COLUMN version INTEGER NOT NULL DEFAULT 1"))

        # --- bsk_duel_rounds ---
        cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(bsk_duel_rounds)"))).fetchall()]
        for col, typ in [
            ("player1_points", "INTEGER"),
            ("player2_points", "INTEGER"),
        ]:
            if col not in cols:
                await conn.execute(text(f"ALTER TABLE bsk_duel_rounds ADD COLUMN {col} {typ}"))

        for pn in (1, 2):
            for comp in ("aim", "speed", "acc", "cons"):
                for phase in ("before", "after"):
                    col = f"p{pn}_mu_{comp}_{phase}"
                    if col not in cols:
                        await conn.execute(text(f"ALTER TABLE bsk_duel_rounds ADD COLUMN {col} REAL"))

        for col, typ in [
            ("ml_predicted_winner", "INTEGER"),
            ("ml_confidence",       "REAL"),
        ]:
            if col not in cols:
                await conn.execute(text(f"ALTER TABLE bsk_duel_rounds ADD COLUMN {col} {typ}"))
