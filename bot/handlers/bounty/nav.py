"""Bounty card navigation: tier switcher + page flip + individual card detail."""
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
_PAGE_SIZE = 5

# ── In-memory nav cache ────────────────────────────────────────────────────
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
    page: int,
    by_tier: dict,
) -> InlineKeyboardMarkup:
    """
    Row 1: tier switcher  [C(N)] [B(N)] [A(N)] [● Open(N)]
    Row 2: page nav  [◀] [Page P/T] [▶]   [Details]
    """
    # Tier row
    tier_row: list[InlineKeyboardButton] = []
    for t in _TIER_ORDER:
        count = len(by_tier.get(t) or [])
        label = f"● {t} ({count})" if t == current_tier else f"{t} ({count})"
        cb = f"boun|tier|{uid}|{t}" if count > 0 else "boun|noop"
        tier_row.append(InlineKeyboardButton(text=label, callback_data=cb))

    # Page nav row
    entries = by_tier.get(current_tier) or []
    total_pages = max(1, (len(entries) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    nav_row: list[InlineKeyboardButton] = []
    nav_row.append(
        InlineKeyboardButton(text="◀", callback_data=f"boun|page|{uid}|{current_tier}|{page-1}")
        if page > 0
        else InlineKeyboardButton(text="◀", callback_data="boun|noop")
    )
    nav_row.append(InlineKeyboardButton(
        text=f"Стр. {page+1}/{total_pages}", callback_data="boun|noop"
    ))
    nav_row.append(
        InlineKeyboardButton(text="▶", callback_data=f"boun|page|{uid}|{current_tier}|{page+1}")
        if page < total_pages - 1
        else InlineKeyboardButton(text="▶", callback_data="boun|noop")
    )
    # Подробно — opens single-card view starting at first card of this page
    start_idx = page * _PAGE_SIZE
    if entries:
        nav_row.append(InlineKeyboardButton(
            text="Подробно",
            callback_data=f"boun|card|{uid}|{current_tier}|{start_idx}",
        ))

    return InlineKeyboardMarkup(inline_keyboard=[tier_row, nav_row])


def bounty_detail_keyboard(
    uid: int,
    bounty_id: str,
    beatmapset_id,
    back_tier: str | None = None,
    tier_idx: int = 0,
    total_in_tier: int = 1,
) -> InlineKeyboardMarkup:
    """
    Row 1: card nav  [◀] [Card I/N] [▶]
    Row 2: actions   [✅ Accept] [🔗 osu!]
    Row 3: back      [← List]
    """
    rows: list[list[InlineKeyboardButton]] = []

    # Card navigation (only if multiple cards in tier)
    if total_in_tier > 1 and back_tier:
        nav = []
        nav.append(
            InlineKeyboardButton(text="◀", callback_data=f"boun|card|{uid}|{back_tier}|{tier_idx-1}")
            if tier_idx > 0
            else InlineKeyboardButton(text="◀", callback_data="boun|noop")
        )
        nav.append(InlineKeyboardButton(
            text=f"{tier_idx+1} / {total_in_tier}", callback_data="boun|noop"
        ))
        nav.append(
            InlineKeyboardButton(text="▶", callback_data=f"boun|card|{uid}|{back_tier}|{tier_idx+1}")
            if tier_idx < total_in_tier - 1
            else InlineKeyboardButton(text="▶", callback_data="boun|noop")
        )
        rows.append(nav)

    action_row = [
        InlineKeyboardButton(text="✅ Принять", callback_data=f"boun|acc|{uid}|{bounty_id}"),
    ]
    if beatmapset_id:
        action_row.append(InlineKeyboardButton(
            text="🔗 osu!", url=f"https://osu.ppy.sh/beatmapsets/{beatmapset_id}",
        ))
    rows.append(action_row)

    if back_tier:
        back_page = tier_idx // _PAGE_SIZE
        rows.append([InlineKeyboardButton(
            text="← Список",
            callback_data=f"boun|page|{uid}|{back_tier}|{back_page}",
        )])

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
        await callback.answer("Не ваш список.", show_alert=True)
        return

    by_tier = get_bounty_nav(uid)
    if not by_tier:
        await callback.answer("Устарело — запустите /bli снова.", show_alert=True)
        return

    await callback.answer()
    await _render_tier_page(callback.message, uid, tier, 0, by_tier, edit=True)


# ── Page flip callback ─────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("boun|page|"))
async def on_bounty_page(callback: types.CallbackQuery) -> None:
    parts = callback.data.split("|", 4)
    if len(parts) != 5:
        await callback.answer()
        return
    _, _, uid_str, tier, page_str = parts
    try:
        uid = int(uid_str)
        page = int(page_str)
    except ValueError:
        await callback.answer()
        return

    if callback.from_user.id != uid:
        await callback.answer("Не ваш список.", show_alert=True)
        return

    by_tier = get_bounty_nav(uid)
    if not by_tier:
        await callback.answer("Устарело — запустите /bli снова.", show_alert=True)
        return

    await callback.answer()
    await _render_tier_page(callback.message, uid, tier, page, by_tier, edit=True)


# ── Card detail callback ───────────────────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("boun|card|"))
async def on_bounty_card(callback: types.CallbackQuery) -> None:
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
        await callback.answer("Не ваш список.", show_alert=True)
        return

    by_tier = get_bounty_nav(uid)
    if not by_tier:
        await callback.answer("Устарело — запустите /bli снова.", show_alert=True)
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
        logger.error(f"bounty card render failed: {e}", exc_info=True)
        await callback.answer("Ошибка рендера.", show_alert=True)
        return

    keyboard = bounty_detail_keyboard(
        uid,
        entry["bounty_id"],
        entry.get("beatmapset_id"),
        back_tier=tier,
        tier_idx=idx,
        total_in_tier=len(entries),
    )
    try:
        await callback.message.edit_media(
            media=InputMediaPhoto(
                media=BufferedInputFile(buf.getvalue(), filename="bounty.jpg"),
            ),
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.debug(f"bounty card edit_media failed: {e}")


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
        await callback.answer("Не ваш баунти.", show_alert=True)
        return

    async with get_db_session() as session:
        user = await get_registered_user(session, uid)
        if not user:
            await callback.answer(
                "Не зарегистрированы. register [nickname]", show_alert=True,
            )
            return

        from bot.handlers.bounty.handlers import _do_accept
        success, msg = await _do_accept(session, user, bounty_id)

    await callback.answer(f"{'✅' if success else '❌'} {msg}", show_alert=True)


# ── Noop callback ──────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data == "boun|noop")
async def on_bounty_noop(callback: types.CallbackQuery) -> None:
    await callback.answer()


# ── Shared render helper ───────────────────────────────────────────────────

async def _render_tier_page(message, uid: int, tier: str, page: int,
                             by_tier: dict, *, edit: bool = False) -> None:
    entries = by_tier.get(tier) or []
    offset = page * _PAGE_SIZE
    page_entries = entries[offset:offset + _PAGE_SIZE]

    from services.image.core import CardRenderer
    renderer = CardRenderer()
    try:
        buf = await renderer.generate_bounty_tier_card_async(tier, page_entries, offset=offset)
    except Exception as e:
        logger.error(f"tier page render failed: {e}", exc_info=True)
        return

    keyboard = bounty_tier_keyboard(uid, tier, page, by_tier)
    try:
        if edit:
            await message.edit_media(
                media=InputMediaPhoto(
                    media=BufferedInputFile(buf.getvalue(), filename="bounty.jpg"),
                ),
                reply_markup=keyboard,
            )
        else:
            await message.answer_photo(
                photo=BufferedInputFile(buf.getvalue(), filename="bounty.jpg"),
                reply_markup=keyboard,
            )
    except Exception as e:
        logger.debug(f"tier page send failed: {e}")
