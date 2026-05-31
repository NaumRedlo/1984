"""Bounty completion event notifications.

Sends a structured message to bounty_notify_chat_id when a submission is
approved — whether by the auto-checker or a manual admin review.  Called
AFTER the DB session is committed so all values are final.

Admin setup: /setbountychat in the target chat stores the chat_id.
"""
from __future__ import annotations

from sqlalchemy import select

from db.database import get_db_session
from db.models.bot_settings import BotSettings
from utils.formatting.text import escape_html
from utils.hp_calculator import get_division_for_hp
from utils.logger import get_logger

logger = get_logger("services.bounty.notify")

_RESULT_LABELS = {
    "win":           "FC ✅",
    "condition":     "Условие ✅",
    "partial":       "Частично ⚡",
    "participation": "Участие 🎮",
}


def _as_int(value) -> int | None:
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None


async def get_bounty_notify_target() -> tuple[int | None, int | None]:
    """(chat_id, thread_id) for bounty result notifications.

    thread_id is the forum topic /setbountychat was run in (None → the
    chat's General topic / a non-forum chat). One query for both keys.
    """
    async with get_db_session() as session:
        rows = (await session.execute(
            select(BotSettings).where(BotSettings.key.in_(
                ["bounty_notify_chat_id", "bounty_notify_thread_id"]
            ))
        )).scalars().all()
    vals = {r.key: r.value for r in rows}
    return (
        _as_int(vals.get("bounty_notify_chat_id")),
        _as_int(vals.get("bounty_notify_thread_id")),
    )


async def send_bounty_event(
    bot,
    *,
    username: str,
    bounty_title: str,
    bounty_type: str | None,
    tier: str | None,
    star_rating: float | None,
    hp_awarded: int,
    result_type: str,
    is_first: bool,
    old_hps: int,
    new_hps: int,
) -> None:
    """Send bounty completion notification. Silent no-op if chat not configured."""
    chat_id, thread_id = await get_bounty_notify_target()
    if not chat_id:
        return

    old_div = get_division_for_hp(old_hps)
    new_div = get_division_for_hp(new_hps)

    tier_str = f"[Tier {tier}] " if tier and tier != "Open" else ""
    sr_str   = f" · {star_rating:.1f}★" if star_rating else ""
    btype    = bounty_type or "Bounty"

    lines = [
        f"🎯 <b>{escape_html(username)}</b> выполнил баунти!",
        f"🗺 {tier_str}{escape_html(bounty_title)}{sr_str} · <i>{escape_html(btype)}</i>",
        f"{_RESULT_LABELS.get(result_type, result_type)}   <b>+{hp_awarded} HP</b>",
        f"📊 {old_hps} → <b>{new_hps} HP</b>",
    ]

    if is_first:
        lines.append("🥇 <b>Авангард</b> — первый игрок на этой карте!")

    if old_div != new_div:
        lines.append(f"📈 {old_div} → <b>{new_div}</b>")

    try:
        await bot.send_message(
            chat_id, "\n".join(lines), parse_mode="HTML",
            message_thread_id=thread_id,
        )
    except Exception as e:
        logger.warning(f"send_bounty_event: failed: {e}")
