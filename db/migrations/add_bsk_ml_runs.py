"""Migration: create bsk_ml_runs table for ML training history."""

from sqlalchemy import text


async def run_bsk_ml_runs_migration(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bsk_ml_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ran_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                rounds_used INTEGER NOT NULL DEFAULT 0,
                maps_updated INTEGER NOT NULL DEFAULT 0,
                maps_skipped INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'ok',
                triggered_by TEXT NOT NULL DEFAULT 'scheduler',
                notes TEXT
            )
        """))
