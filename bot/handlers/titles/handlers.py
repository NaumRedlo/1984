"""TITLES COLLECTION dashboard — `titles` / `tt`.

One rendered card with inline buttons for rarity filtering and pagination.
Following the bounty-nav pattern: the per-viewer dataset (progress + summary +
avatar) is computed once and cached with a TTL, so button presses only re-slice
and re-render — no DB/API round-trip per click.
"""

from datetime import timedelta
from typing import Dict, Optional

from aiogram import Router, types
from aiogram.types import (
    BufferedInputFile, InputMediaPhoto,
    InlineKeyboardButton, InlineKeyboardMarkup,
)

from sqlalchemy import select

from db.database import get_db_session
from db.models.title_progress import UserTitleProgress
from db.models.user import User
from services.image import card_renderer
from services.image.render.titles import build_titles_card_data
from services.image.utils import download_image
from services.refresh import refresh_user, needs_blocking_refresh
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.osu.resolve_user import get_reply_target_user
from utils.title_progress import build_titles_summary, calc_title_rarity, refresh_user_titles
from utils.titles import RARITY_META, RARITY_ORDER, TITLE_REGISTRY
from utils.timeutils import utcnow
from bot.filters import TextTriggerFilter, TriggerArgs
from bot.handlers.common.auth import require_registered_user
from bot.utils.safe_edit import safe_edit_media

router = Router(name="titles")
logger = get_logger("handlers.titles")

# ── Per-viewer nav cache ───────────────────────────────────────────────────
_NAV_CACHE: Dict[int, dict] = {}
_TTL = timedelta(minutes=15)


def _store_nav(uid: int, payload: dict) -> None:
    payload["expires_at"] = utcnow() + _TTL
    _NAV_CACHE[uid] = payload


def _get_nav(uid: int) -> Optional[dict]:
    rec = _NAV_CACHE.get(uid)
    if not rec:
        return None
    if utcnow() > rec["expires_at"]:
        del _NAV_CACHE[uid]
        return None
    return rec


def _tg_handle(from_user) -> Optional[str]:
    username = getattr(from_user, "username", None) if from_user else None
    return f"@{username}" if username else None


# ── Keyboard ───────────────────────────────────────────────────────────────
_FILTERS = [("all", "ALL")] + [(r, RARITY_META[r]["label"]) for r in RARITY_ORDER]


def _titles_keyboard(uid: int, flt: str, page: int, total_pages: int, is_owner: bool = False) -> InlineKeyboardMarkup:
    btns = [
        InlineKeyboardButton(
            text=(f"● {lbl}" if code == flt else lbl),
            callback_data=f"tt|f|{uid}|{code}",
        )
        for code, lbl in _FILTERS
    ]
    rows = [btns[:4], btns[4:]]
    if total_pages > 1:
        rows.append([
            InlineKeyboardButton(text="◀", callback_data=f"tt|p|{uid}|{flt}|{page-1}")
            if page > 0 else InlineKeyboardButton(text="◀", callback_data="tt|x"),
            InlineKeyboardButton(text=f"Стр. {page+1}/{total_pages}", callback_data="tt|x"),
            InlineKeyboardButton(text="▶", callback_data=f"tt|p|{uid}|{flt}|{page+1}")
            if page < total_pages - 1 else InlineKeyboardButton(text="▶", callback_data="tt|x"),
        ])
    if is_owner:
        rows.append([InlineKeyboardButton(text="⭐ Выбрать титул", callback_data=f"tt|pick|{uid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _main_kb(uid: int, payload: dict) -> InlineKeyboardMarkup:
    """Rebuild the dashboard keyboard for the card's current filter/page."""
    return _titles_keyboard(
        uid, payload.get("cur_flt", "all"), payload.get("cur_page", 0),
        payload.get("cur_total_pages", 1), payload.get("is_owner", False),
    )


def _picker_keyboard(uid: int, unlocked: list, active_code) -> InlineKeyboardMarkup:
    """A keyboard of the user's UNLOCKED titles (2/row) to pick the active one."""
    rows, row = [], []
    for p in unlocked:
        label = ("● " if p["code"] == active_code else "") + p["name"]
        row.append(InlineKeyboardButton(text=label, callback_data=f"tt|set|{uid}|{p['code']}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([
        InlineKeyboardButton(text="✕ Снять", callback_data=f"tt|set|{uid}|__off__"),
        InlineKeyboardButton(text="← Назад", callback_data=f"tt|back|{uid}"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Render ─────────────────────────────────────────────────────────────────

async def _render(message, uid: int, flt: str, page: int, payload: dict, *, edit: bool) -> None:
    import asyncio

    data = build_titles_card_data(
        payload["username"], payload["handle"], payload["country"],
        payload["progress"], payload["summary"],
        filter=flt, page=page, rarest_global_pct=payload["rarest_pct"],
    )
    payload["cur_flt"] = data["filter"]
    payload["cur_page"] = data["page"]
    payload["cur_total_pages"] = data["total_pages"]
    buf = await asyncio.to_thread(card_renderer.generate_titles_card, data, payload.get("avatar"))
    kb = _titles_keyboard(uid, data["filter"], data["page"], data["total_pages"], payload.get("is_owner", False))
    file = BufferedInputFile(buf.getvalue(), filename="titles.png")
    try:
        if edit:
            await safe_edit_media(message, media=InputMediaPhoto(media=file), reply_markup=kb)
        else:
            await message.answer_photo(photo=file, reply_markup=kb)
    except Exception as e:
        logger.debug(f"titles render send failed: {e}")


async def _build_payload(session, user, tg_handle: Optional[str]) -> dict:
    progress = await refresh_user_titles(user, session)
    await session.commit()
    summary = build_titles_summary(progress)
    rarest_pct = None
    if summary["rarest"]:
        rarest_pct = await calc_title_rarity(summary["rarest"]["code"], session)
    avatar = None
    av_url = getattr(user, "avatar_url", None)
    if av_url:
        try:
            avatar = await download_image(av_url)
        except Exception:
            avatar = None
    return {
        "progress": progress,
        "summary": summary,
        "username": getattr(user, "osu_username", "???"),
        "handle": tg_handle,
        "country": getattr(user, "country", None),
        "avatar": avatar,
        "rarest_pct": rarest_pct,
        "owner_user_id": getattr(user, "id", None),
        "owner_tg_id": getattr(user, "telegram_id", None),
        "active_code": getattr(user, "active_title_code", None),
    }


# ── Command ────────────────────────────────────────────────────────────────

@router.message(TextTriggerFilter("tt"))
async def show_titles(message: types.Message, osu_api_client=None, trigger_args: TriggerArgs = None, tenant_chat_id=None):
    tg_id = message.from_user.id
    async with get_db_session() as session:
        try:
            tg_handle = None
            reply_user = await get_reply_target_user(session, message, chat_id=tenant_chat_id)
            if reply_user and reply_user.osu_user_id:
                user = reply_user
                if message.reply_to_message:
                    tg_handle = _tg_handle(message.reply_to_message.from_user)
            else:
                user = await require_registered_user(session, message=message, tenant_chat_id=tenant_chat_id)
                if not user:
                    return
                tg_handle = _tg_handle(message.from_user)
                # Freshen self data if stale (also recomputes titles inside refresh_user).
                if osu_api_client and getattr(user, "telegram_id", None) == tg_id \
                        and needs_blocking_refresh(user.last_api_update):
                    wait = await message.answer("Загрузка свежих данных из osu!...")
                    ok = await refresh_user(user, session, osu_api_client, mode="full")
                    if ok:
                        await session.commit()
                        await session.refresh(user)
                        await wait.delete()
                    else:
                        await wait.edit_text("Не удалось обновить, показаны кешированные данные.")

            payload = await _build_payload(session, user, tg_handle)
            payload["is_owner"] = payload.get("owner_tg_id") == tg_id
            _store_nav(tg_id, payload)
            await _render(message, tg_id, "all", 0, payload, edit=False)
        except Exception as e:
            logger.error(f"Error in /titles for {tg_id}: {e}", exc_info=True)
            await message.answer("Произошла ошибка при загрузке коллекции титулов.")


# ── Callbacks ──────────────────────────────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("tt|f|"))
async def on_titles_filter(callback: types.CallbackQuery) -> None:
    parts = callback.data.split("|", 3)
    if len(parts) != 4:
        await callback.answer()
        return
    _, _, uid_str, code = parts
    await _navigate(callback, uid_str, code, 0)


@router.callback_query(lambda c: c.data and c.data.startswith("tt|p|"))
async def on_titles_page(callback: types.CallbackQuery) -> None:
    parts = callback.data.split("|", 4)
    if len(parts) != 5:
        await callback.answer()
        return
    _, _, uid_str, code, page_str = parts
    try:
        page = int(page_str)
    except ValueError:
        await callback.answer()
        return
    await _navigate(callback, uid_str, code, page)


@router.callback_query(lambda c: c.data == "tt|x")
async def on_titles_noop(callback: types.CallbackQuery) -> None:
    await callback.answer()


async def _navigate(callback: types.CallbackQuery, uid_str: str, code: str, page: int) -> None:
    try:
        uid = int(uid_str)
    except ValueError:
        await callback.answer()
        return
    if callback.from_user.id != uid:
        await callback.answer("Не ваша коллекция.", show_alert=True)
        return
    if code != "all" and code not in RARITY_META:
        await callback.answer()
        return
    payload = _get_nav(uid)
    if not payload:
        await callback.answer("Устарело — запустите titles снова.", show_alert=True)
        return
    await callback.answer()
    await _render(callback.message, uid, code, page, payload, edit=True)


# ── Active-title picker (owner only) ────────────────────────────────────────

@router.callback_query(lambda c: c.data and c.data.startswith("tt|pick|"))
async def on_titles_pick(callback: types.CallbackQuery) -> None:
    payload, uid = _owner_payload(callback)
    if payload is None:
        await callback.answer("Устарело — запустите titles снова.", show_alert=True)
        return
    unlocked = [p for p in payload["progress"] if p["unlocked"]]
    if not unlocked:
        await callback.answer("Пока нет открытых титулов.", show_alert=True)
        return
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(
            reply_markup=_picker_keyboard(uid, unlocked, payload.get("active_code")))
    except Exception as e:
        logger.debug(f"titles picker show failed: {e}")


@router.callback_query(lambda c: c.data and c.data.startswith("tt|set|"))
async def on_titles_set(callback: types.CallbackQuery) -> None:
    parts = callback.data.split("|", 3)
    if len(parts) != 4:
        await callback.answer()
        return
    code = parts[3]
    payload, uid = _owner_payload(callback)
    if payload is None:
        await callback.answer("Устарело — запустите titles снова.", show_alert=True)
        return
    new_code = None if code == "__off__" else code
    unlocked_codes = {p["code"] for p in payload["progress"] if p["unlocked"]}
    if new_code is not None and new_code not in unlocked_codes:
        await callback.answer("Этот титул ещё не открыт.", show_alert=True)
        return
    owner_id = payload.get("owner_user_id")
    if owner_id:
        async with get_db_session() as session:
            u = await session.get(User, owner_id)
            if u:
                u.active_title_code = new_code
                await session.commit()
    payload["active_code"] = new_code
    if new_code is None:
        await callback.answer("Титул снят. Виден в pf.")
    else:
        td = TITLE_REGISTRY.get(new_code)
        await callback.answer(f"★ Активный титул: {td.name if td else new_code}")
    try:
        await callback.message.edit_reply_markup(reply_markup=_main_kb(uid, payload))
    except Exception as e:
        logger.debug(f"titles set back failed: {e}")


@router.callback_query(lambda c: c.data and c.data.startswith("tt|back|"))
async def on_titles_back(callback: types.CallbackQuery) -> None:
    payload, uid = _owner_payload(callback)
    if payload is None:
        await callback.answer("Устарело — запустите titles снова.", show_alert=True)
        return
    await callback.answer()
    try:
        await callback.message.edit_reply_markup(reply_markup=_main_kb(uid, payload))
    except Exception as e:
        logger.debug(f"titles back failed: {e}")


def _owner_payload(callback: types.CallbackQuery):
    """Validate a picker callback's ownership; return (payload, uid) or (None, uid)."""
    try:
        uid = int(callback.data.split("|", 3)[2])
    except (ValueError, IndexError):
        return None, 0
    if callback.from_user.id != uid:
        return None, uid
    payload = _get_nav(uid)
    if not payload or not payload.get("is_owner"):
        return None, uid
    return payload, uid


# ── settitle command ────────────────────────────────────────────────────────

async def _unlocked_codes(session, user_id: int) -> set:
    rows = await session.execute(
        select(UserTitleProgress.title_code).where(
            UserTitleProgress.user_id == user_id,
            UserTitleProgress.unlocked == True,  # noqa: E712
        )
    )
    return {r[0] for r in rows.all()}


@router.message(TextTriggerFilter("st"))
async def set_title_cmd(message: types.Message, trigger_args: TriggerArgs = None, tenant_chat_id=None):
    arg = (trigger_args.args or "").strip() if trigger_args else ""
    async with get_db_session() as session:
        user = await require_registered_user(session, message=message, tenant_chat_id=tenant_chat_id)
        if not user:
            return
        if not arg:
            await message.answer(
                "Использование: <code>st &lt;имя&gt;</code> или <code>st off</code>.",
                parse_mode="HTML")
            return
        if arg.lower() in ("off", "none", "clear", "снять", "-", "—"):
            user.active_title_code = None
            await session.commit()
            await message.answer("Титул снят.")
            return
        unlocked = await _unlocked_codes(session, user.id)
        ql = arg.lower()
        matches = [(c, TITLE_REGISTRY[c]) for c in unlocked
                   if c in TITLE_REGISTRY and ql in TITLE_REGISTRY[c].name.lower()]
        exact = [m for m in matches if m[1].name.lower() == ql]
        if exact:
            matches = exact
        if not matches:
            await message.answer(
                f"Нет открытого титула по запросу «{escape_html(arg)}».", parse_mode="HTML")
            return
        if len(matches) > 1:
            names = ", ".join(td.name for _, td in matches[:8])
            await message.answer(
                f"Уточни — подходит несколько: {escape_html(names)}.", parse_mode="HTML")
            return
        code, td = matches[0]
        user.active_title_code = code
        await session.commit()
        await message.answer(
            f"★ Активный титул: <b>{escape_html(td.name)}</b> ({td.rarity_label}). Виден в pf.",
            parse_mode="HTML")


__all__ = ["router"]
