"""Duel recovery after a bot restart.

Any duel left ``round_active`` is resumed by re-launching its round-engine
task, which picks up from ``Duel.current_round``.  IRC channels are rejoined
separately by ``irc_room.rejoin_active_duel_channels`` (wired on reconnect in
``bot/main.py``).
"""

from sqlalchemy import select

from db.database import get_db_session
from db.models.duel import Duel
from utils.logger import get_logger

logger = get_logger("duel.recover")


async def recover_active_duels(bot, osu_api) -> None:
    from services.duel import round_engine

    async with get_db_session() as session:
        duel_ids = (await session.execute(
            select(Duel.id).where(Duel.status == 'round_active')
        )).scalars().all()

    if not duel_ids:
        logger.info("recover_active_duels: nothing to resume")
        return

    logger.info(f"recover_active_duels: resuming {len(duel_ids)} duel(s)")
    for duel_id in duel_ids:
        round_engine.launch(bot, osu_api, duel_id)
