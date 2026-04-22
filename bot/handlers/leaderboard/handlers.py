import asyncio
from datetime import datetime, timezone, timedelta

from aiogram import Router, types, F
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    BufferedInputFile,
    InputMediaPhoto,
)
from aiogram.exceptions import TelegramBadRequest
from db.database import get_db_session
from services.image import leaderboard_gen
from services.leaderboard import (
    CATEGORIES,
    build_category_card,
    build_map_leaderboard,
    map_leaderboard_usage,
    schedule_stale_refresh,
)
from utils.logger import get_logger
from utils.osu.helpers import extract_beatmap_id, get_message_context
from utils.formatting.text import escape_html
from bot.filters import TextTriggerFilter, TriggerArgs

router = Router(name="leaderboard")
logger = get_logger("handlers.leaderboard")

# Keyboard

def get_leaderboard_keyboard(active_key: str = "hp", page: int = 0, total_pages: int = 1) -> InlineKeyboardMarkup:
    """Category buttons + pagination row."""
    keys = list(CATEGORIES.keys())
    # Layout: rows of 3, last row may have fewer
    rows = [keys[i:i + 3] for i in range(0, len(keys), 3)]
    keyboard = []
    for row_keys in rows:
        row = []
        for k in row_keys:
            cat = CATEGORIES[k]
            label = f"• {cat['btn']} •" if k == active_key else cat["btn"]
            row.append(InlineKeyboardButton(text=label, callback_data=f"lb:{k}:{0}"))
        keyboard.append(row)

    # Pagination row
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀", callback_data=f"lb:{active_key}:{page - 1}"))
    nav_row.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="lb:noop:0"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="▶", callback_data=f"lb:{active_key}:{page + 1}"))
    keyboard.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


# Handlers

@router.message(TextTriggerFilter("leaderboard", "lb", "top"))
async def show_leaderboard(message: types.Message, trigger_args: TriggerArgs = None, osu_api_client=None):
    async with get_db_session() as session:
        try:
            photo, page, total_pages, entries = await build_category_card(session, "pp", 0)
            await message.answer_photo(
                photo=photo,
                reply_markup=get_leaderboard_keyboard("pp", page, total_pages),
            )
            schedule_stale_refresh(entries, osu_api_client)
        except Exception as e:
            logger.error(f"Error in /leaderboard: {e}", exc_info=True)
            await message.answer("Произошла ошибка при загрузке таблицы лидеров.")


@router.message(TextTriggerFilter("leaderboardmap", "lbm"))
async def show_map_leaderboard(message: types.Message, trigger_args: TriggerArgs = None, osu_api_client=None):
    user_input = (trigger_args.args or "").strip() if trigger_args else ""
    beatmap_id = None
    map_title = None
    map_version = None

    # 1. From args (ID or URL)
    if user_input:
        beatmap_id = extract_beatmap_id(user_input)

    # 2. From reply context
    if not beatmap_id and message.reply_to_message:
        reply = message.reply_to_message
        context = get_message_context(reply.chat.id, reply.message_id)
        if context:
            beatmap_id = context.get("beatmap_id") or context.get("beatmap")
            if context.get("artist") and context.get("title"):
                map_title = f"{context['artist']} - {context['title']}"
            map_version = context.get("version")
        if not beatmap_id:
            probe = reply.caption or reply.text or ""
            beatmap_id = extract_beatmap_id(probe)

    if not beatmap_id:
        await message.answer(map_leaderboard_usage(), parse_mode="HTML")
        return

    await _send_map_leaderboard(message, int(beatmap_id), osu_api_client, map_title, map_version)


@router.callback_query(F.data.startswith("lbm:"))
async def map_leaderboard_callback(callback: CallbackQuery, osu_api_client=None):
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer()
        return

    if parts[1] == "noop":
        await callback.answer()
        return

    if not parts[1].isdigit():
        await callback.answer("Некорректные данные.")
        return

    beatmap_id = int(parts[1])
    page = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 0
    is_page_nav = len(parts) >= 3  # page navigation vs new lbm from rs card

    await callback.answer()
    if is_page_nav:
        await _send_map_leaderboard(callback.message, beatmap_id, osu_api_client, page=page, edit=True)
    else:
        await _send_map_leaderboard(callback.message, beatmap_id, osu_api_client, page=0)


def _calc_lbm_total_pages(num_rows: int) -> int:
    """Calculate total pages for map leaderboard pagination."""
    LBM_FIRST_PAGE_ROWS = 6  # positions 4-9
    LBM_PAGE_ROWS = 5
    if num_rows <= 3 + LBM_FIRST_PAGE_ROWS:
        return 1
    remaining = num_rows - 3 - LBM_FIRST_PAGE_ROWS
    return 1 + max((remaining + LBM_PAGE_ROWS - 1) // LBM_PAGE_ROWS, 1)


def _build_lbm_keyboard(beatmap_id: int, beatmapset_id: int, page: int, total_pages: int) -> InlineKeyboardMarkup:
    """Build inline keyboard for map leaderboard with pagination."""
    beatmap_url = f"https://osu.ppy.sh/beatmapsets/{beatmapset_id}#osu/{beatmap_id}"
    rows = []
    # Navigation row (only if >1 page)
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="◀", callback_data=f"lbm:{beatmap_id}:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="lbm:noop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="▶", callback_data=f"lbm:{beatmap_id}:{page + 1}"))
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="Карта", url=beatmap_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_map_leaderboard(message: types.Message, beatmap_id: int, osu_api_client, map_title=None, map_version=None, page: int = 0, edit: bool = False):
    """Shared logic for lbm command and callback."""
    wait_msg = None
    if not edit:
        wait_msg = await message.answer("Загрузка лидерборда...", parse_mode="HTML")

    async with get_db_session() as session:
        try:
            result = await build_map_leaderboard(session, osu_api_client, beatmap_id, sync=not edit)
            rows = result.rows
            beatmapset_id = result.beatmapset_id
            total_pages = result.total_pages
            page = max(0, min(page, total_pages - 1))

            data = dict(result.data)
            data["page"] = page
            if map_title:
                data["map_title"] = map_title
            if map_version:
                data["map_version"] = map_version

            kb = _build_lbm_keyboard(beatmap_id, beatmapset_id, page, total_pages)

            try:
                photo = await leaderboard_gen.generate_map_leaderboard_card_async(data)
                buf = BufferedInputFile(photo.read(), filename="map_leaderboard.png")

                if edit:
                    try:
                        await message.edit_media(
                            media=InputMediaPhoto(media=buf),
                            reply_markup=kb,
                        )
                    except TelegramBadRequest as e:
                        if "message is not modified" not in str(e):
                            raise
                else:
                    await wait_msg.delete()
                    await message.answer_photo(photo=buf, reply_markup=kb)
                schedule_stale_refresh(rows, osu_api_client)
            except Exception as img_err:
                logger.warning(f"Map leaderboard card generation failed: {img_err}")
                text = [
                    f"<b>Map leaderboard</b> — {escape_html(data.get('map_title') or 'Unknown map')}",
                    f"Beatmap ID: <code>{beatmap_id:,}</code>",
                    f"<b>PLAYS:</b> {int(data.get('total_plays') or 0):,}",
                ]
                if rows:
                    text.append("\n<b>Top players:</b>")
                    for row in rows[:10]:
                        text.append(f"#{row['position']} {escape_html(row['username'])} — {row['value']}")
                else:
                    text.append("\nЭту карту ещё не сыграл ни один зарегистрированный пользователь.")
                if edit:
                    await message.answer("\n".join(text), parse_mode="HTML")
                elif wait_msg:
                    await wait_msg.edit_text("\n".join(text), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in lbm: {e}", exc_info=True)
            err_text = "Не удалось построить leaderboard по карте."
            if edit:
                await message.answer(err_text)
            elif wait_msg:
                await wait_msg.edit_text(err_text)


@router.callback_query(F.data.startswith("lb:"))
async def leaderboard_callback(callback: CallbackQuery, osu_api_client=None):
    parts = callback.data.split(":")
    if len(parts) != 3:
        await callback.answer()
        return

    _, key, page_str = parts

    if key == "noop":
        await callback.answer()
        return

    if key not in CATEGORIES:
        await callback.answer("Неизвестная категория", show_alert=True)
        return

    try:
        page = max(int(page_str), 0)
    except ValueError:
        page = 0

    async with get_db_session() as session:
        try:
            photo, page, total_pages, entries = await build_category_card(session, key, page)
            media = InputMediaPhoto(media=photo)
            await callback.message.edit_media(
                media=media,
                reply_markup=get_leaderboard_keyboard(key, page, total_pages),
            )
            schedule_stale_refresh(entries, osu_api_client)
        except Exception as e:
            logger.error(f"Error in leaderboard callback '{key}' page {page}: {e}", exc_info=True)
            await callback.answer("Ошибка при обновлении лидерборда", show_alert=True)
            return

    await callback.answer()


__all__ = ["router"]
