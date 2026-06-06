"""Duel recovery after a bot restart.

Resumes in-flight duels by re-launching their round-engine task:
  * ``round_active`` — mid-play; the engine picks up from ``Duel.current_round``.
  * ``accepted`` — crashed mid-setup (IRC room / pool build never finished, since
    those are committed atomically with the flip to ``round_active``). The engine's
    own guard finishes a ready one or cancels a half-built one, so launching it is
    the clean way to unstick these.

Still-``pending`` duels are handled too: a restart during the accept window would
otherwise strand them forever (blocking the per-user "one active duel" guard).
Past-deadline pendings are expired immediately; the rest get their expiry timer
re-armed.

IRC channels are rejoined separately by ``irc_room.rejoin_active_duel_channels``
(wired on reconnect in ``bot/main.py``).
"""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from db.database import get_db_session
from db.models.duel import Duel
from utils.logger import get_logger

logger = get_logger("duel.recover")


async def recover_active_duels(bot, osu_api) -> None:
    from services.duel import round_engine
    from services.duel.duel_manager import _expire_duel  # lazy: avoids import cycle

    now = datetime.now(timezone.utc)

    async with get_db_session() as session:
        resume_ids = (await session.execute(
            select(Duel.id).where(Duel.status.in_(('accepted', 'round_active')))
        )).scalars().all()

        pending = (await session.execute(
            select(Duel).where(Duel.status == 'pending')
        )).scalars().all()
        expired: list[tuple] = []
        rearm: list[int] = []
        for d in pending:
            exp = d.expires_at
            if exp and exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if exp and now > exp:
                d.status = 'expired'
                expired.append((d.id, d.chat_id, d.message_id))
            else:
                rearm.append(d.id)
        await session.commit()

    for duel_id in resume_ids:
        round_engine.launch(bot, osu_api, duel_id)
    for duel_id in rearm:
        asyncio.create_task(_expire_duel(bot, duel_id))

    # Best-effort: tell the topic the stranded challenge expired.
    if expired:
        from utils.telegram_safe import safe_edit_text
        for _duel_id, chat_id, message_id in expired:
            if chat_id and message_id:
                try:
                    await safe_edit_text(
                        bot, "⌛ <b>Вызов истёк</b> — соперник не ответил вовремя.",
                        chat_id=chat_id, message_id=message_id, parse_mode="HTML",
                    )
                except Exception:
                    logger.debug("recover: expire-edit failed", exc_info=True)

    if resume_ids or rearm or expired:
        logger.info(
            f"recover_active_duels: resumed={len(resume_ids)} "
            f"pending_rearmed={len(rearm)} pending_expired={len(expired)}"
        )
    else:
        logger.info("recover_active_duels: nothing to resume")
