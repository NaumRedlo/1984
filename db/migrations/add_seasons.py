"""Migration: add seasons, season_snapshots tables and related columns."""
from datetime import datetime, timezone

from sqlalchemy import text


async def run_add_seasons_migration(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS seasons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number INTEGER NOT NULL UNIQUE,
                started_at DATETIME NOT NULL,
                ended_at DATETIME,
                is_active INTEGER NOT NULL DEFAULT 1
            )
        """))

        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS season_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                season_id INTEGER NOT NULL REFERENCES seasons(id),
                user_id INTEGER NOT NULL REFERENCES users(id),
                hps_points INTEGER NOT NULL DEFAULT 0,
                hps_division TEXT NOT NULL DEFAULT 'Candidate III',
                duel_conservative REAL,
                duel_division TEXT,
                UNIQUE(season_id, user_id)
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_season_snapshots_season_id "
            "ON season_snapshots(season_id)"
        ))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_season_snapshots_user_id "
            "ON season_snapshots(user_id)"
        ))

        # Add season_bonus_hps to users if missing
        try:
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN season_bonus_hps INTEGER NOT NULL DEFAULT 0"
            ))
        except Exception:
            pass

        # Insert Season 1 if no seasons exist
        result = await conn.execute(text("SELECT COUNT(*) FROM seasons"))
        count = result.scalar()
        if not count:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            await conn.execute(text(
                "INSERT INTO seasons (number, started_at, is_active) VALUES (1, :now, 1)"
            ), {"now": now})
