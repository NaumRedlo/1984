import asyncio
from typing import Optional

from aiogram import Bot

from utils.formatting.text import escape_html
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger

logger = get_logger("oauth.linked")

SHOWN_FOR = 10


def notifier(bot: Bot):
    async def linked(telegram_id: int, osu_username: str, link_msg: Optional[tuple[int, int]] = None) -> None:
        logger.info(f"linked: tg={telegram_id}, link_msg={link_msg}")
        if not link_msg:
            logger.warning(f"linked: no pending message for tg={telegram_id}")
            return
        chat_id, msg_id = link_msg
        try:
            await bot.delete_message(chat_id, msg_id)
        except Exception as e:
            logger.warning(f"Failed to delete link message: {e}")

        lang = (await get_language(telegram_id)).lower()
        success_msg = await bot.send_message(
            chat_id,
            t("oauth.notify_linked", lang, username=escape_html(osu_username)),
            parse_mode="HTML",
        )
        await asyncio.sleep(SHOWN_FOR)
        try:
            await success_msg.delete()
        except Exception as e:
            logger.warning(f"Failed to delete success message: {e}")

    return linked
