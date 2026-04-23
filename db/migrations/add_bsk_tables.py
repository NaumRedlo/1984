"""Migration: create bsk_ratings table."""

from sqlalchemy import text


async def run_bsk_migration(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bsk_ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id),
                mode TEXT NOT NULL DEFAULT 'casual',
                mu_aim   REAL NOT NULL DEFAULT 250.0,
                mu_speed REAL NOT NULL DEFAULT 250.0,
                mu_acc   REAL NOT NULL DEFAULT 250.0,
                mu_cons  REAL NOT NULL DEFAULT 250.0,
                sigma_aim   REAL NOT NULL DEFAULT 100.0,
                sigma_speed REAL NOT NULL DEFAULT 100.0,
                sigma_acc   REAL NOT NULL DEFAULT 100.0,
                sigma_cons  REAL NOT NULL DEFAULT 100.0,
                placement_matches_left INTEGER NOT NULL DEFAULT 10,
                wins   INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, mode)
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_bsk_ratings_user_id ON bsk_ratings(user_id)"
        ))
