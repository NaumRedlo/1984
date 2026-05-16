"""Migration: add beatmapset_id and mapper fields to bounties.

`beatmapset_id` lets the renderer hit assets.ppy.sh directly for the cover.
`mapper_*` columns let BOUNTY DETAIL show the mapper under the song title
without re-querying the osu! API on every render. Idempotent.
"""

import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)


async def run_bounty_beatmapset_id_migration(engine):
    async with engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(bounties)"))
        existing = {row[1] for row in result.fetchall()}

        adds = [
            ("beatmapset_id", "INTEGER"),
            ("mapper_id", "INTEGER"),
            ("mapper_name", "TEXT"),
            ("mapper_avatar_url", "TEXT"),
        ]
        for col, typ in adds:
            if col not in existing:
                await conn.execute(text(f"ALTER TABLE bounties ADD COLUMN {col} {typ}"))
                logger.info(f"Migration: added column bounties.{col}")
