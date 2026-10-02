import logging

from sqlalchemy import text

from db.models.witness_session import WitnessSession

logger = logging.getLogger(__name__)

async def run_witness_sessions_migration(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: WitnessSession.__table__.create(sync, checkfirst=True))
        result = await conn.execute(text("PRAGMA table_info(players)"))
        existing = {row[1] for row in result.fetchall()}
        if "time_zone" not in existing:
            await conn.execute(text("ALTER TABLE players ADD COLUMN time_zone VARCHAR(64)"))
            logger.info("Migration: added column players.time_zone")
