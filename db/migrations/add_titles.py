"""
Migration: add title system fields.
- users.active_title_code (String)
- user_best_scores.star_rating (Float)
- user_title_progress table (new)
Safe for SQLite — checks before ALTER/CREATE.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def run_titles_migration(engine):
    """Add title-related columns and table. Idempotent."""
    async with engine.begin() as conn:
        # 1. users.active_title_code
        result = await conn.execute(text("PRAGMA table_info(users)"))
        user_cols = {row[1] for row in result.fetchall()}

        if "active_title_code" not in user_cols:
            await conn.execute(text(
                "ALTER TABLE users ADD COLUMN active_title_code VARCHAR(50)"
            ))
            logger.info("Migration: added column users.active_title_code")

        # 2. user_best_scores.star_rating
        result = await conn.execute(text("PRAGMA table_info(user_best_scores)"))
        score_cols = {row[1] for row in result.fetchall()}

        if "star_rating" not in score_cols:
            await conn.execute(text(
                "ALTER TABLE user_best_scores ADD COLUMN star_rating FLOAT"
            ))
            logger.info("Migration: added column user_best_scores.star_rating")

        # 3. user_title_progress table
        result = await conn.execute(text(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='user_title_progress'"
        ))
        if not result.fetchone():
            await conn.execute(text("""
                CREATE TABLE user_title_progress (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    title_code VARCHAR(50) NOT NULL,
                    current_value INTEGER NOT NULL DEFAULT 0,
                    unlocked BOOLEAN NOT NULL DEFAULT 0,
                    unlocked_at DATETIME,
                    UNIQUE(user_id, title_code)
                )
            """))
            await conn.execute(text(
                "CREATE INDEX ix_user_title_progress_user_id ON user_title_progress(user_id)"
            ))
            logger.info("Migration: created table user_title_progress")
