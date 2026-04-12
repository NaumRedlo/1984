"""
Migration: add duel system tables and fields.
- users.duel_wins, users.duel_losses (Integer)
- duels table (new)
- duel_rounds table (new)
Safe for SQLite — checks before ALTER/CREATE.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def run_duels_migration(engine):
    """Add duel-related columns and tables. Idempotent."""
    async with engine.begin() as conn:
        # 1. users.duel_wins, users.duel_losses
        result = await conn.execute(text("PRAGMA table_info(users)"))
        user_cols = {row[1] for row in result.fetchall()}

        if "duel_wins" not in user_cols:
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN duel_wins INTEGER NOT NULL DEFAULT 0"
            ))
            logger.info("Migration: added column users.duel_wins")

        if "duel_losses" not in user_cols:
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN duel_losses INTEGER NOT NULL DEFAULT 0"
            ))
            logger.info("Migration: added column users.duel_losses")

        # 2. duels table
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='duels'"
        ))
        if not result.fetchone():
            await conn.execute(text("""
                CREATE TABLE duels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    player1_user_id INTEGER NOT NULL REFERENCES users(id),
                    player2_user_id INTEGER NOT NULL REFERENCES users(id),
                    best_of INTEGER NOT NULL DEFAULT 5,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    player1_rounds_won INTEGER NOT NULL DEFAULT 0,
                    player2_rounds_won INTEGER NOT NULL DEFAULT 0,
                    winner_user_id INTEGER REFERENCES users(id),
                    chat_id INTEGER,
                    message_id INTEGER,
                    created_at DATETIME NOT NULL,
                    completed_at DATETIME
                )
            """))
            logger.info("Migration: created table duels")

        # 3. duel_rounds table
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='duel_rounds'"
        ))
        if not result.fetchone():
            await conn.execute(text("""
                CREATE TABLE duel_rounds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    duel_id INTEGER NOT NULL REFERENCES duels(id),
                    round_number INTEGER NOT NULL,
                    beatmap_id INTEGER NOT NULL,
                    beatmap_title VARCHAR(255),
                    star_rating FLOAT,
                    player1_score INTEGER,
                    player1_accuracy FLOAT,
                    player1_combo INTEGER,
                    player2_score INTEGER,
                    player2_accuracy FLOAT,
                    player2_combo INTEGER,
                    winner_user_id INTEGER REFERENCES users(id),
                    completed_at DATETIME
                )
            """))
            await conn.execute(text(
                "CREATE INDEX ix_duel_rounds_duel_id ON duel_rounds(duel_id)"
            ))
            logger.info("Migration: created table duel_rounds")
