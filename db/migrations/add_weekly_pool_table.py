"""Migration: create weekly_bounty_pool table.

Plan: unified-giggling-tiger.  Each row represents one weekly bounty cycle
(Monday 00:00 MSK → next Monday).  The weekly generator inserts a new row
each Monday and marks the previous one is_active=0.  Bounty.week_id is an
FK reference (logical, not enforced by SQLite for backward-compat).
"""

from sqlalchemy import text


async def run_weekly_pool_table_migration(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS weekly_bounty_pool (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_number INTEGER NOT NULL,
                started_at DATETIME NOT NULL,
                ends_at DATETIME NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            )
        """))
        await conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_weekly_pool_active "
            "ON weekly_bounty_pool(is_active)"
        ))
