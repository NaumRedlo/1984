"""Migration: add hp_drain column to bsk_map_pool."""
import logging
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


async def run_bsk_hp_drain_migration(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        existing = {
            row[1]
            for row in (await conn.execute(text("PRAGMA table_info(bsk_map_pool)"))).fetchall()
        }
        if "hp_drain" not in existing:
            await conn.execute(text("ALTER TABLE bsk_map_pool ADD COLUMN hp_drain REAL"))
            await conn.execute(text("UPDATE bsk_map_pool SET hp_drain = 0 WHERE hp_drain IS NULL"))
            logger.info("add_bsk_hp_drain: added column bsk_map_pool.hp_drain")
