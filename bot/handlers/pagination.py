"""Shared callback handler for inline page navigation (pg|prefix|user_id|page)."""

from aiogram import Router, types

from bot.utils.paginator import get_pages, nav_keyboard
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="pagination")


@router.callback_query(lambda c: c.data and c.data.startswith("pg|"))
async def handle_page_turn(callback: types.CallbackQuery) -> None:
    if callback.data == "pg|noop":
        await callback.answer()
        return

    parts = callback.data.split("|")
    if len(parts) != 4:
        await callback.answer()
        return

    _, prefix, user_id_str, page_str = parts
    try:
        user_id = int(user_id_str)
        page = int(page_str)
    except ValueError:
        await callback.answer()
        return

    lang = (await get_language(callback.from_user.id)).lower()
    if callback.from_user.id != user_id:
        await callback.answer(t("common.not_your_list", lang), show_alert=True)
        return

    pages = get_pages(prefix, user_id)
    if not pages:
        await callback.answer(t("common.pages_stale", lang), show_alert=True)
        return

    if not (0 <= page < len(pages)):
        await callback.answer()
        return

    keyboard = nav_keyboard(prefix, user_id, page, len(pages))
    try:
        await callback.message.edit_text(pages[page], parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.debug(f"page_turn edit failed: {e}")
    await callback.answer()
