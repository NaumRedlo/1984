"""Telegram transport helpers for BSK duels."""

from io import BytesIO
from typing import Optional

from aiogram import Bot
from aiogram.types import BufferedInputFile, InputMediaPhoto

from utils.logger import get_logger

logger = get_logger("bsk.duel_telegram")


async def send_or_edit_photo(
    bot: Bot,
    chat_id: int,
    message_id: Optional[int],
    img_bytes,
    caption: str = "",
    reply_markup=None,
    thread_id: Optional[int] = None,
) -> Optional[int]:
    """
    Send a new photo message or edit an existing one.

    Returns the actual message_id. It may differ from the input message_id if
    editing fails and a fallback send is used.
    """
    if isinstance(img_bytes, BytesIO):
        img_bytes.seek(0)
        raw = img_bytes.read()
    else:
        raw = img_bytes

    file = BufferedInputFile(raw, filename="duel.png")

    if message_id is None:
        msg = await bot.send_photo(
            chat_id,
            photo=file,
            caption=caption or None,
            parse_mode="HTML" if caption else None,
            reply_markup=reply_markup,
            message_thread_id=thread_id,
        )
        return msg.message_id

    try:
        await bot.edit_message_media(
            media=InputMediaPhoto(
                media=file,
                caption=caption or None,
                parse_mode="HTML" if caption else None,
            ),
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
        )
    except Exception as e:
        logger.warning(f"send_or_edit_photo edit failed ({e}), sending new message")
        msg = await bot.send_photo(
            chat_id,
            photo=BufferedInputFile(raw, filename="duel.png"),
            caption=caption or None,
            parse_mode="HTML" if caption else None,
            reply_markup=reply_markup,
            message_thread_id=thread_id,
        )
        return msg.message_id

    return message_id
