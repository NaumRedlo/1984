"""DUEL division change notification service."""
from aiogram import Bot

from db.database import get_db_session
from db.models.user import User
from utils.hp_calculator import DUEL_DIVISION_INDEX
from utils.logger import get_logger
from sqlalchemy import select

logger = get_logger("duel.division_notify")


async def _get_duel_notify_chat_id() -> int | None:
    from db.models.bot_settings import BotSettings
    async with get_db_session() as session:
        row = (await session.execute(
            select(BotSettings).where(BotSettings.key == "duel_notify_chat_id")
        )).scalar_one_or_none()
        if row and row.value:
            try:
                return int(row.value)
            except ValueError:
                return None
    return None


async def notify_division_change(
    bot: Bot,
    user_id: int,
    old_div: str,
    new_div: str,
    duel_chat_id: int,
    duel_thread_id: int | None,
    duel_points: float | None = None,
    mode: str = "ranked",
) -> None:
    is_promotion = DUEL_DIVISION_INDEX[new_div] > DUEL_DIVISION_INDEX[old_div]
    arrow = "⬆️" if is_promotion else "⬇️"

    async with get_db_session() as session:
        user = (await session.execute(
            select(User).where(User.id == user_id)
        )).scalar_one_or_none()
        if not user:
            return
        username = user.osu_username
        avatar_url = user.avatar_url
        cover_data = bytes(user.cover_data) if user.cover_data else None
        country = user.country or ""

    try:
        from services.image import card_renderer
        from datetime import datetime, timezone
        img_bytes = await card_renderer.generate_duel_division_card_async({
            "username": username,
            "country": country,
            "avatar_url": avatar_url,
            "cover_data": cover_data,
            "old_div": old_div,
            "new_div": new_div,
            "is_promotion": is_promotion,
            "duel_points": duel_points,
            "mode": mode,
            "occurred_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        })
    except Exception as e:
        logger.error(f"notify_division_change: card generation failed: {e}", exc_info=True)
        img_bytes = None

    caption = (
        f"{arrow} <b>{username}</b> {'поднялся' if is_promotion else 'опустился'} "
        f"до <b>{new_div}</b>!"
    )

    chat_ids = [duel_chat_id]
    notify_chat = await _get_duel_notify_chat_id()
    if notify_chat and notify_chat != duel_chat_id:
        chat_ids.append(notify_chat)

    for cid in chat_ids:
        thread = duel_thread_id if cid == duel_chat_id else None
        try:
            if img_bytes:
                from aiogram.types import BufferedInputFile
                await bot.send_photo(
                    cid,
                    BufferedInputFile(img_bytes.getvalue(), filename="division.png"),
                    caption=caption,
                    parse_mode="HTML",
                    message_thread_id=thread,
                )
            else:
                await bot.send_message(cid, caption, parse_mode="HTML", message_thread_id=thread)
        except Exception as e:
            logger.warning(f"notify_division_change: send to {cid} failed: {e}")
