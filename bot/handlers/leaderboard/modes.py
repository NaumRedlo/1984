import asyncio

from aiogram import F, Router, types
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.dm_tenant import ensure_dm_tenant
from bot.utils.safe_edit import safe_edit_media
from db.database import get_db_session
from services.leaderboard import modes
from services.leaderboard.delta_card import build_absolute_payload
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger
from utils.osu import rulesets
from utils.osu.resolve_user import get_registered_user

logger = get_logger("handlers.leaderboard.modes")
router = Router(name="leaderboard_modes")

_SHORT = {0: "osu!", 1: "taiko", 2: "catch", 3: "mania"}


def keyboard(ruleset: int, page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=f"• {name} •" if rid == ruleset else name, callback_data=f"rk:{rid}:0")
             for rid, name in _SHORT.items()]]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀", callback_data=f"rk:{ruleset}:{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton(text="▶", callback_data=f"rk:{ruleset}:{page + 1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _card(chat_id: int, ruleset: int, page: int, viewer_tg_id: int, client, lang: str):
    from services.image.render.leaderboard_delta import render_delta_leaderboard

    async with get_db_session() as session:
        if await modes.refresh(session, client, chat_id, ruleset):
            await session.commit()
        viewer = await get_registered_user(session, viewer_tg_id, chat_id)
        entries = await modes.standings(session, chat_id, ruleset)
        board = modes.board(entries, page, viewer.id if viewer else None)
    payload = build_absolute_payload(board, lang)
    payload["title"] = t("rk.title", lang, mode=rulesets.TITLES[ruleset])
    payload["empty_label"] = t("rk.empty", lang)
    png = await asyncio.to_thread(render_delta_leaderboard, payload)
    return BufferedInputFile(png, filename=f"ranking_{ruleset}.png"), board


@router.message(TextTriggerFilter("ranking", "rk"))
async def cmd_ranking(message: types.Message, trigger_args: TriggerArgs = None, osu_api_client=None,
                      tenant_chat_id=None):
    if not await ensure_dm_tenant(message, tenant_chat_id):
        return
    lang = (await get_language(message.from_user.id)).lower()
    ruleset, _ = rulesets.split_args((trigger_args.args if trigger_args else "") or "")
    ruleset = ruleset or 0
    try:
        photo, board = await _card(tenant_chat_id, ruleset, 0, message.from_user.id, osu_api_client, lang)
        await message.answer_photo(photo=photo, reply_markup=keyboard(ruleset, board["page"], board["total_pages"]))
    except Exception as exc:
        logger.error(f"ranking {ruleset} failed: {exc}", exc_info=True)
        await message.answer(t("lb.load_error", lang))


@router.callback_query(F.data.startswith("rk:"))
async def ranking_callback(callback: CallbackQuery, osu_api_client=None, tenant_chat_id=None):
    lang = (await get_language(callback.from_user.id)).lower()
    try:
        _, rid, page = callback.data.split(":")
        ruleset, page = int(rid), max(0, int(page))
    except ValueError:
        await callback.answer()
        return
    if ruleset not in rulesets.RULESETS:
        await callback.answer()
        return
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    await callback.answer()
    try:
        photo, board = await _card(tenant_chat_id, ruleset, page, callback.from_user.id, osu_api_client, lang)
        await safe_edit_media(callback.message, media=InputMediaPhoto(media=photo),
                              reply_markup=keyboard(ruleset, board["page"], board["total_pages"]))
    except Exception as exc:
        logger.error(f"ranking {ruleset} page {page} failed: {exc}", exc_info=True)


__all__ = ["router"]
