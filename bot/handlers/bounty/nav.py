"""Bounty card inline-navigation: tier switcher + per-slot detail view."""
from datetime import datetime, timedelta

from aiogram import Router, types
from aiogram.types import (
    BufferedInputFile, InputMediaPhoto,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from db.database import get_db_session
from utils.osu.resolve_user import get_registered_user
from utils.logger import get_logger

logger = get_logger(__name__)

router = Router(name="bounty_nav")

_TIER_ORDER = ("C", "B", "A", "Open")

# ── In-memory nav cache ────────────────────────────────────────────────────
# Structure: {"boun:{uid}": {"by_tier": {tier: [entry, ...]}, "expires_at": ...}}
_NAV_CACHE: dict[str, dict] = {}
_TTL = timedelta(minutes=15)


def store_bounty_nav(uid: int, by_tier: dict) -> None:
    _NAV_CACHE[f"boun:{uid}"] = {
        "by_tier": by_tier,
        "expires_at": datetime.utcnow() + _TTL,
    }


def get_bounty_nav(uid: int) -> dict | None:
    key = f"boun:{uid}"
    rec = _NAV_CACHE.get(key)
    if not rec:
        return None
    if datetime.utcnow() > rec["expires_at"]:
        del _NAV_CACHE[key]
        return None
    return rec["by_tier"]


# ── Keyboard builders ──────────────────────────────────────────────────────

def bounty_tier_keyboard(
    uid: int,
    current_tier: str,
    by_tier: dict,
) -> InlineKeyboardMarkup:
    """Tier switcher row + slot buttons for current tier's entries."""
    tier_row: list[InlineKeyboardButton] = []
    for t in _TIER_ORDER:
        count = len(by_tier.get(t) or [])
        label = f"● {t} ({count})" if t == current_tier else f"{t} ({count})"
        cb = f"boun|tier|{uid}|{t}" if count > 0 else "boun|noop"
        tier_row.append(InlineKeyboardButton(text=label, callback_data=cb))

    rows: list[list[InlineKeyboardButton]] = [tier_row]

    # Slot buttons for the entries currently shown (up to 5)
    entries = (by_tier.get(current_tier) or [])[:5]
    if entries:
        slot_row = [
            InlineKeyboardButton(
                text=str(i + 1),
                callback_data=f"boun|det|{uid}|{current_tier}|{i}",
            )
            for i in range(len(entries))
        ]
        rows.append(slot_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def bounty_detail_keyboard(
    uid: int,
    bounty_id: str,
    beatmapset_id,
    back_tier: str | None = None,
) -> InlineKeyboardMarkup:
    """Accept + osu! link; optional '← List' button when opened from tier view."""
    action_row = [
        InlineKeyboardButton(
            text="✅ Accept",
            callback_data=f"boun|acc|{uid}|{bounty_id}",
        ),
    ]
    if beatmapset_id:
        action_row.append(InlineKeyboardButton(
            text="🔗 osu!",
            url=f"https://osu.ppy.sh/beatmapsets/{beatmapset_id}",
        ))

    rows = [action_row]
    if back_tier:
        rows.append([
            InlineKeyboardButton(
                text="← List",
                callback_data=f"boun|tier|{uid}|{back_tier}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Tier switch callback ───────────────────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("boun|tier|"))
async def on_bounty_tier(callback: types.CallbackQuery) -> None:
    parts = callback.data.split("|", 3)
    if len(parts) != 4:
        await callback.answer()
        return
    _, _, uid_str, tier = parts
    try:
        uid = int(uid_str)
    except ValueError:
        await callback.answer()
        return

    if callback.from_user.id != uid:
        await callback.answer("Это не ваш список.", show_alert=True)
        return

    by_tier = get_bounty_nav(uid)
    if not by_tier:
        await callback.answer("Список устарел — запросите /bli снова.", show_alert=True)
        return

    entries = by_tier.get(tier) or []
    await callback.answer()

    from services.image.core import CardRenderer
    renderer = CardRenderer()
    try:
        buf = await renderer.generate_bounty_tier_card_async(tier, entries)
    except Exception as e:
        logger.error(f"bounty tier card render failed: {e}", exc_info=True)
        await callback.answer("Ошибка рендеринга карточки.", show_alert=True)
        return

    keyboard = bounty_tier_keyboard(uid, tier, by_tier)
    try:
        await callback.message.edit_media(
            media=InputMediaPhoto(
                media=BufferedInputFile(buf.getvalue(), filename="bounty.jpg"),
            ),
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.debug(f"bounty tier edit_media failed: {e}")


# ── Slot detail callback ───────────────────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("boun|det|"))
async def on_bounty_det(callback: types.CallbackQuery) -> None:
    parts = callback.data.split("|", 4)
    if len(parts) != 5:
        await callback.answer()
        return
    _, _, uid_str, tier, idx_str = parts
    try:
        uid = int(uid_str)
        idx = int(idx_str)
    except ValueError:
        await callback.answer()
        return

    if callback.from_user.id != uid:
        await callback.answer("Это не ваш список.", show_alert=True)
        return

    by_tier = get_bounty_nav(uid)
    if not by_tier:
        await callback.answer("Список устарел — запросите /bli снова.", show_alert=True)
        return

    entries = by_tier.get(tier) or []
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
        logger.error(f"bounty det card render failed: {e}", exc_info=True)
        await callback.answer("Ошибка рендеринга карточки.", show_alert=True)
        return

    keyboard = bounty_detail_keyboard(
        uid,
        entry["bounty_id"],
        entry.get("beatmapset_id"),
        back_tier=tier,
    )
    try:
        await callback.message.edit_media(
            media=InputMediaPhoto(
                media=BufferedInputFile(buf.getvalue(), filename="bounty.jpg"),
            ),
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.debug(f"bounty det edit_media failed: {e}")


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
                "Not registered. Use: register [nickname]",
                show_alert=True,
            )
            return

        from bot.handlers.bounty.handlers import _do_accept
        success, msg = await _do_accept(session, user, bounty_id)

    await callback.answer(f"{'✅' if success else '❌'} {msg}", show_alert=True)


# ── Noop callback ──────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "boun|noop")
async def on_bounty_noop(callback: types.CallbackQuery) -> None:
    await callback.answer()
