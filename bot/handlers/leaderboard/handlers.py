
from aiogram import Router, types, F
from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    BufferedInputFile,
    InputMediaPhoto,
)
from db.database import get_db_session
from services.image import card_renderer
from services.leaderboard import (
    CATEGORIES,
    build_absolute_card,
    build_delta_card,
    build_map_leaderboard,
    map_leaderboard_usage,
    schedule_stale_refresh,
)
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger
from utils.osu.helpers import extract_beatmap_id, get_message_context
from utils.osu.resolve_user import get_registered_user
from utils.formatting.text import escape_html
from bot.handlers.dm_tenant import ensure_dm_tenant
from bot.filters import TextTriggerFilter, TriggerArgs
from bot.utils.safe_edit import safe_edit_media

router = Router(name="leaderboard")
logger = get_logger("handlers.leaderboard")

# Keyboard

def get_leaderboard_keyboard(active_key: str = "pp", page: int = 0, total_pages: int = 1,
                             lang: str = "en", mode: str = "absolute") -> InlineKeyboardMarkup:
    """Category buttons + mode toggle + pagination row.

    Callback shape is ``lb:<key>:<page>:<mode>``. The mode segment is optional on
    the way IN so buttons from messages sent before this feature still work
    (missing -> "absolute").
    """
    keys = list(CATEGORIES.keys())
    # Layout: rows of 3, last row may have fewer
    rows = [keys[i:i + 3] for i in range(0, len(keys), 3)]
    keyboard = []
    for row_keys in rows:
        row = []
        for k in row_keys:
            label_text = t(f"lb.cat.{k}", lang)
            label = f"• {label_text} •" if k == active_key else label_text
            row.append(InlineKeyboardButton(text=label, callback_data=f"lb:{k}:0:{mode}"))
        keyboard.append(row)

    # Mode toggle — shows what you'd switch TO.
    other = "absolute" if mode == "delta" else "delta"
    keyboard.append([InlineKeyboardButton(
        text=t(f"lb.mode.{other}", lang), callback_data=f"lb:{active_key}:0:{other}")])

    # Pagination — both modes page now. No counter in the middle: the arrows
    # already say whether there's more, and the card carries the context.
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(text="◀", callback_data=f"lb:{active_key}:{page - 1}:{mode}"))
    if page < total_pages - 1:
        nav_row.append(InlineKeyboardButton(text="▶", callback_data=f"lb:{active_key}:{page + 1}:{mode}"))
    if nav_row:
        keyboard.append(nav_row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _viewer_user_id(session, telegram_id: int, chat_id: int):
    """DB id of the person pressing the button — for their pinned row."""
    from utils.osu.resolve_user import get_registered_user
    user = await get_registered_user(session, telegram_id, chat_id)
    return user.id if user else None


# Handlers

@router.message(TextTriggerFilter("lb", "top"))
async def show_leaderboard(message: types.Message, trigger_args: TriggerArgs = None, osu_api_client=None, tenant_chat_id=None):
    if not await ensure_dm_tenant(message, tenant_chat_id):
        return
    lang = (await get_language(message.from_user.id)).lower() if message.from_user else "en"
    chat_id = tenant_chat_id
    async with get_db_session() as session:
        try:
            viewer_id = await _viewer_user_id(session, message.from_user.id, chat_id)
            photo, board = await build_absolute_card(
                session, "pp", chat_id, 0, viewer_user_id=viewer_id, lang=lang)
            await message.answer_photo(
                photo=photo,
                reply_markup=get_leaderboard_keyboard(
                    "pp", board["page"], board["total_pages"], lang),
            )
            schedule_stale_refresh(board["entries"], osu_api_client)
        except Exception as e:
            logger.error(f"Error in /leaderboard: {e}", exc_info=True)
            await message.answer(t("lb.load_error", lang))


@router.message(TextTriggerFilter("lbm"))
async def show_map_leaderboard(message: types.Message, trigger_args: TriggerArgs = None, osu_api_client=None, tenant_chat_id=None):
    if not await ensure_dm_tenant(message, tenant_chat_id):
        return
    lang = (await get_language(message.from_user.id)).lower() if message.from_user else "en"
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
        await message.answer(map_leaderboard_usage(lang), parse_mode="HTML")
        return

    await _send_map_leaderboard(message, int(beatmap_id), osu_api_client, map_title, map_version,
                                tenant_chat_id=tenant_chat_id, lang=lang)


@router.callback_query(F.data.startswith("lbm:"))
async def map_leaderboard_callback(callback: CallbackQuery, osu_api_client=None, tenant_chat_id=None):
    lang = (await get_language(callback.from_user.id)).lower() if callback.from_user else "en"
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer()
        return

    if parts[1] == "noop":
        await callback.answer()
        return

    if not parts[1].isdigit():
        await callback.answer(t("lb.bad_data", lang))
        return

    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return

    beatmap_id = int(parts[1])
    page = int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 0
    is_page_nav = len(parts) >= 3  # page navigation vs new lbm from rs card

    await callback.answer()
    if is_page_nav:
        await _send_map_leaderboard(callback.message, beatmap_id, osu_api_client, page=page, edit=True,
                                    tenant_chat_id=tenant_chat_id, lang=lang)
    else:
        await _send_map_leaderboard(callback.message, beatmap_id, osu_api_client, page=0,
                                    tenant_chat_id=tenant_chat_id, lang=lang)


def _build_lbm_keyboard(beatmap_id: int, beatmapset_id: int, page: int, total_pages: int,
                        lang: str = "en") -> InlineKeyboardMarkup:
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
    rows.append([InlineKeyboardButton(text=t("common.kb.beatmap", lang), url=beatmap_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_map_leaderboard(message: types.Message, beatmap_id: int, osu_api_client, map_title=None,
                                map_version=None, page: int = 0, edit: bool = False, tenant_chat_id=None,
                                lang: str = "en"):
    """Shared logic for lbm command and callback."""
    chat_id = tenant_chat_id if tenant_chat_id is not None else message.chat.id
    wait_msg = None
    if not edit:
        wait_msg = await message.answer(t("lbm.loading", lang), parse_mode="HTML")

    async with get_db_session() as session:
        try:
            result = await build_map_leaderboard(session, osu_api_client, beatmap_id, chat_id, sync=not edit)
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

            # Who is looking, so the card can mark their row — and pull it out
            # beneath the board when this page does not happen to hold it.
            viewer_row = None
            viewer = await get_registered_user(session, message.from_user.id, chat_id) \
                if message.from_user else None
            if viewer and viewer.osu_username:
                viewer_row = next(
                    (r for r in rows if r.get("username") == viewer.osu_username), None
                )
            data["viewer"] = viewer_row
            # The map's own name and difficulty, which the card sets separately
            # rather than as one "artist - title" line.
            title = data.get("map_title") or ""
            artist, _, name = title.partition(" - ")
            data["title"] = name or title
            data["artist"] = artist if name else ""
            data["version"] = data.get("map_version")
            data["footer"] = t("lbm.footer", lang)

            kb = _build_lbm_keyboard(beatmap_id, beatmapset_id, page, total_pages, lang)

            try:
                photo = await card_renderer.generate_map_leaderboard_v2_async(data)
                buf = BufferedInputFile(photo.read(), filename="map_leaderboard.png")

                if edit:
                    await safe_edit_media(
                        message,
                        media=InputMediaPhoto(media=buf),
                        reply_markup=kb,
                    )
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
                    text.append(f"\n{t('lbm.no_plays', lang)}")
                if edit:
                    await message.answer("\n".join(text), parse_mode="HTML")
                elif wait_msg:
                    await wait_msg.edit_text("\n".join(text), parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in lbm: {e}", exc_info=True)
            err_text = t("lbm.build_failed", lang)
            if edit:
                await message.answer(err_text)
            elif wait_msg:
                await wait_msg.edit_text(err_text)


@router.callback_query(F.data.startswith("lb:"))
async def leaderboard_callback(callback: CallbackQuery, osu_api_client=None, tenant_chat_id=None):
    lang = (await get_language(callback.from_user.id)).lower() if callback.from_user else "en"
    parts = callback.data.split(":")
    if len(parts) not in (3, 4):
        await callback.answer()
        return

    key, page_str = parts[1], parts[2]
    # Older messages carry no mode segment — they mean the all-time board.
    mode = parts[3] if len(parts) == 4 and parts[3] in ("absolute", "delta") else "absolute"

    if key == "noop":
        await callback.answer()
        return

    if key not in CATEGORIES:
        await callback.answer(t("lb.unknown_category", lang), show_alert=True)
        return

    try:
        page = max(int(page_str), 0)
    except ValueError:
        page = 0

    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    chat_id = tenant_chat_id

    async with get_db_session() as session:
        try:
            if mode == "delta":
                viewer_id = await _viewer_user_id(session, callback.from_user.id, chat_id)
                photo, board = await build_delta_card(
                    session, key, chat_id, page, viewer_user_id=viewer_id, lang=lang)
                await safe_edit_media(
                    callback.message,
                    media=InputMediaPhoto(media=photo),
                    reply_markup=get_leaderboard_keyboard(
                        key, board["page"], board["total_pages"], lang, mode="delta"),
                )
            else:
                viewer_id = await _viewer_user_id(session, callback.from_user.id, chat_id)
                photo, board = await build_absolute_card(
                    session, key, chat_id, page, viewer_user_id=viewer_id, lang=lang)
                await safe_edit_media(
                    callback.message,
                    media=InputMediaPhoto(media=photo),
                    reply_markup=get_leaderboard_keyboard(
                        key, board["page"], board["total_pages"], lang),
                )
                schedule_stale_refresh(board["entries"], osu_api_client)
        except Exception as e:
            logger.error(f"Error in leaderboard callback '{key}' page {page} mode {mode}: {e}", exc_info=True)
            await callback.answer(t("lb.update_error", lang), show_alert=True)
            return

    await callback.answer()


__all__ = ["router"]
