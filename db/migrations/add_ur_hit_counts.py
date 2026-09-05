import logging

from sqlalchemy import text

from db.migrations._utils import existing_columns, table_exists

logger = logging.getLogger(__name__)

async def run_ur_hit_counts_migration(engine) -> None:
    async with engine.begin() as conn:

        if not await table_exists(conn, "submissions"):
            logger.debug("Migration: no submissions table — skipping hit counts")
            return

        sub_cols = await existing_columns(conn, "submissions")
        sub_new = [
            ("n_300", "INTEGER"),
            ("n_100", "INTEGER"),
            ("n_50",  "INTEGER"),
            ("ur_est", "FLOAT"),
        ]
        for col, typedef in sub_new:
            if col not in sub_cols:
                await conn.execute(text(f"ALTER TABLE submissions ADD COLUMN {col} {typedef}"))
