"""Migration: BSK duel pause votes + ML run accuracy tracking."""

from sqlalchemy import text


async def run_bsk_pause_ml_accuracy_migration(engine) -> None:
    async with engine.begin() as conn:
        # --- bsk_duels ---
        cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(bsk_duels)"))).fetchall()]
        for col, typ, default in [
            ("pause_votes", "INTEGER", "0"),
            ("paused_at",   "DATETIME", None),
        ]:
            if col not in cols:
                if default is not None:
                    await conn.execute(text(f"ALTER TABLE bsk_duels ADD COLUMN {col} {typ} NOT NULL DEFAULT {default}"))
                else:
                    await conn.execute(text(f"ALTER TABLE bsk_duels ADD COLUMN {col} {typ}"))

        # --- bsk_ml_runs ---
        cols = [row[1] for row in (await conn.execute(text("PRAGMA table_info(bsk_ml_runs)"))).fetchall()]
        for col, typ in [
            ("predictions_total",   "INTEGER"),
            ("predictions_correct", "INTEGER"),
            ("prediction_accuracy", "REAL"),
        ]:
            if col not in cols:
                await conn.execute(text(f"ALTER TABLE bsk_ml_runs ADD COLUMN {col} {typ}"))
