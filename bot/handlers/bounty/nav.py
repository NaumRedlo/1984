"""Bounty card inline-navigation callbacks and nav cache."""
from datetime import datetime, timedelta

from aiogram import Router, types
from aiogram.types import (
    BufferedInputFile, InputMediaPhoto,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from db.database import get_db_session
from utils.osu.resolve_user import get_registered_user
from utils.formatting.text import escape_html, format_error, format_success
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="bounty_nav")

# ── In-memory nav cache ────────────────────────────────────────────────────
_NAV_CACHE: dict[str, dict] = {}
_TTL = timedelta(minutes=15)


def store_bounty_nav(uid: int, entries: list) -> None:
    key = f"boun:{uid}"
    _NAV_CACHE[key] = {
        "entries": entries,
        "expires_at": datetime.utcnow() + _TTL,
    }


def get_bounty_nav(uid: int) -> list | None:
    key = f"boun:{uid}"
    entry = _NAV_CACHE.get(key)
    if not entry:
        return None
    if datetime.utcnow() > entry["expires_at"]:
        del _NAV_CACHE[key]
        return None
    return entry["entries"]


def bounty_list_keyboard(
    uid: int, idx: int, total: int,
    bounty_id: str, beatmapset_id,
) -> InlineKeyboardMarkup:
    def nav_cb(i: int) -> str:
        return f"boun|nav|{uid}|{i}"

    nav_row = []
    nav_row.append(
        InlineKeyboardButton(text="◀", callback_data=nav_cb(idx - 1))
        if idx > 0
        else InlineKeyboardButton(text="◀", callback_data="boun|noop")
    )
    nav_row.append(InlineKeyboardButton(
        text=f"{idx + 1} / {total}", callback_data="boun|noop"
    ))
    nav_row.append(
        InlineKeyboardButton(text="▶", callback_data=nav_cb(idx + 1))
        if idx < total - 1
        else InlineKeyboardButton(text="▶", callback_data="boun|noop")
    )

    action_row = [
        InlineKeyboardButton(text="✅ Принять", callback_data=f"boun|acc|{uid}|{bounty_id}"),
    ]
    if beatmapset_id:
        action_row.append(InlineKeyboardButton(
            text="🔗 osu!", url=f"https://osu.ppy.sh/beatmapsets/{beatmapset_id}"
        ))

    return InlineKeyboardMarkup(inline_keyboard=[nav_row, action_row])


def bounty_detail_keyboard(
    uid: int, bounty_id: str, beatmapset_id,
) -> InlineKeyboardMarkup:
    row = [
        InlineKeyboardButton(text="✅ Принять", callback_data=f"boun|acc|{uid}|{bounty_id}"),
    ]
    if beatmapset_id:
        row.append(InlineKeyboardButton(
            text="🔗 osu!", url=f"https://osu.ppy.sh/beatmapsets/{beatmapset_id}"
        ))
    return InlineKeyboardMarkup(inline_keyboard=[row])


# ── Navigation callback ────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("boun|nav|"))
async def on_bounty_nav(callback: types.CallbackQuery) -> None:
    parts = callback.data.split("|", 3)
    if len(parts) != 4:
        await callback.answer()
        return
    _, _, uid_str, idx_str = parts
    try:
        uid = int(uid_str)
        idx = int(idx_str)
    except ValueError:
        await callback.answer()
        return

    if callback.from_user.id != uid:
        await callback.answer("Это не ваш список.", show_alert=True)
        return

    entries = get_bounty_nav(uid)
    if not entries:
        await callback.answer("Список устарел — запросите /bli снова.", show_alert=True)
        return

    if not (0 <= idx < len(entries)):
        await callback.answer()
        return

    await callback.answer()

    entry = entries[idx]
    from services.image.core import CardRenderer
    renderer = CardRenderer()
    try:
        buf = await renderer.generate_bounty_compact_card_async(entry)
    except Exception as e:
        logger.error(f"bounty nav card render failed: {e}", exc_info=True)
        await callback.answer("Ошибка рендеринга карточки.", show_alert=True)
        return

    keyboard = bounty_list_keyboard(
        uid, idx, len(entries),
        entry["bounty_id"], entry.get("beatmapset_id"),
    )
    try:
        await callback.message.edit_media(
            media=InputMediaPhoto(
                media=BufferedInputFile(buf.getvalue(), filename="bounty.jpg"),
            ),
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.debug(f"bounty nav edit_media failed: {e}")


# ── Accept callback ────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("boun|acc|"))
async def on_bounty_accept(callback: types.CallbackQuery) -> None:
    parts = callback.data.split("|", 3)
    if len(parts) != 4:
        await callback.answer()
        return
    _, _, uid_str, bounty_id = parts
    try:
        uid = int(uid_str)
    except ValueError:
        await callback.answer()
        return

    if callback.from_user.id != uid:
        await callback.answer("Это не ваш баунти.", show_alert=True)
        return

    async with get_db_session() as session:
        user = await get_registered_user(session, uid)
        if not user:
            await callback.answer(
                "Вы не зарегистрированы. Используйте register [nickname]",
                show_alert=True,
            )
            return

        from bot.handlers.bounty.handlers import _do_accept
        success, msg = await _do_accept(session, user, bounty_id)

    if success:
        await callback.answer(f"✅ {msg}", show_alert=True)
    else:
        await callback.answer(f"❌ {msg}", show_alert=True)


# ── Noop callback ──────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "boun|noop")
async def on_bounty_noop(callback: types.CallbackQuery) -> None:
    await callback.answer()
