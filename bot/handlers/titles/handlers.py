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
from services.image import card_renderer
from services.image.render.titles import build_titles_card_data
from services.image.utils import download_image
from services.refresh import refresh_user, needs_blocking_refresh
from utils.formatting.text import escape_html
from utils.i18n import t
from utils.logger import get_logger
from utils.osu.resolve_user import get_reply_target_user
from utils.title_progress import build_titles_summary, calc_title_rarity, refresh_user_titles
from utils.titles import RARITY_META, RARITY_ORDER, TITLE_REGISTRY
from utils.timeutils import utcnow
from utils.language import get_language
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


def _titles_keyboard(uid: int, flt: str, page: int, total_pages: int, lang: str = "en") -> InlineKeyboardMarkup:
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
            InlineKeyboardButton(text=t("tpp.kb.page", lang, page=page + 1, total=total_pages), callback_data="tt|x"),
            InlineKeyboardButton(text="▶", callback_data=f"tt|p|{uid}|{flt}|{page+1}")
            if page < total_pages - 1 else InlineKeyboardButton(text="▶", callback_data="tt|x"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _main_kb(uid: int, payload: dict) -> InlineKeyboardMarkup:
    """Rebuild the dashboard keyboard for the card's current filter/page."""
    return _titles_keyboard(
        uid, payload.get("cur_flt", "all"), payload.get("cur_page", 0),
        payload.get("cur_total_pages", 1), payload.get("lang", "en"),
    )


# ── Render ─────────────────────────────────────────────────────────────────

async def _render(message, uid: int, flt: str, page: int, payload: dict, *, edit: bool) -> None:
    import asyncio

    data = build_titles_card_data(
        payload["username"], payload["handle"], payload["country"],
        payload["progress"], payload["summary"],
        filter=flt, page=page, rarest_global_pct=payload["rarest_pct"],
    )
    data["lang"] = payload.get("lang", "en")
    payload["cur_flt"] = data["filter"]
    payload["cur_page"] = data["page"]
    payload["cur_total_pages"] = data["total_pages"]
    buf = await asyncio.to_thread(card_renderer.generate_titles_card, data, payload.get("avatar"))
    kb = _titles_keyboard(uid, data["filter"], data["page"], data["total_pages"], payload.get("lang", "en"))
    file = BufferedInputFile(buf.getvalue(), filename="titles.png")
    try:
        if edit:
            await safe_edit_media(message, media=InputMediaPhoto(media=file), reply_markup=kb)
        else:
            await message.answer_photo(photo=file, reply_markup=kb)
    except Exception as e:
        logger.debug(f"titles render send failed: {e}")


async def _build_payload(session, user, tg_handle: Optional[str], viewer_tg_id: Optional[int] = None) -> dict:
    # Card text (incl. title name/description/rarity_label baked into progress
    # below) follows the VIEWER's language (2026-07-05 fix — used to follow
    # the SUBJECT's, e.g. looking up someone else's collection via a reply
    # rendered in THEIR language instead of the requester's).
    card_lang = await get_language(viewer_tg_id if viewer_tg_id is not None else user.telegram_id)
    progress = await refresh_user_titles(user, session, lang=card_lang.lower())
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
        "lang": card_lang,
    }


# ── Command ────────────────────────────────────────────────────────────────

@router.message(TextTriggerFilter("tt"))
async def show_titles(message: types.Message, osu_api_client=None, trigger_args: TriggerArgs = None, tenant_chat_id=None):
    tg_id = message.from_user.id
    lang = (await get_language(tg_id)).lower()
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
                    wait = await message.answer(t("pf.refreshing", lang))
                    ok = await refresh_user(user, session, osu_api_client, mode="full")
                    if ok:
                        await session.commit()
                        await session.refresh(user)
                        await wait.delete()
                    else:
                        await wait.edit_text(t("tpp.refreshing_cached_fallback", lang))

            payload = await _build_payload(session, user, tg_handle, viewer_tg_id=tg_id)
            _store_nav(tg_id, payload)
            await _render(message, tg_id, "all", 0, payload, edit=False)
        except Exception as e:
            logger.error(f"Error in /titles for {tg_id}: {e}", exc_info=True)
            await message.answer(t("tt.load_error", lang))


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
    lang = (await get_language(callback.from_user.id)).lower()
    if callback.from_user.id != uid:
        await callback.answer(t("tt.not_your_collection", lang), show_alert=True)
        return
    if code != "all" and code not in RARITY_META:
        await callback.answer()
        return
    payload = _get_nav(uid)
    if not payload:
        await callback.answer(t("tt.stale", lang), show_alert=True)
        return
    await callback.answer()
    await _render(callback.message, uid, code, page, payload, edit=True)


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
    lang = (await get_language(message.from_user.id)).lower() if message.from_user else "en"
    arg = (trigger_args.args or "").strip() if trigger_args else ""
    async with get_db_session() as session:
        user = await require_registered_user(session, message=message, tenant_chat_id=tenant_chat_id)
        if not user:
            return
        if not arg:
            await message.answer(t("st.usage", lang), parse_mode="HTML")
            return
        # "снять" (Russian for "take off") is accepted regardless of UI language
        # — a typed-input alias, not output text.
        if arg.lower() in ("off", "none", "clear", "снять", "-", "—"):
            user.active_title_code = None
            await session.commit()
            await message.answer(t("st.cleared", lang))
            return
        unlocked = await _unlocked_codes(session, user.id)
        ql = arg.lower()
        matches = [(c, TITLE_REGISTRY[c]) for c in unlocked
                   if c in TITLE_REGISTRY and ql in TITLE_REGISTRY[c].name.lower()]
        exact = [m for m in matches if m[1].name.lower() == ql]
        if exact:
            matches = exact
        if not matches:
            await message.answer(t("st.not_found", lang, query=escape_html(arg)), parse_mode="HTML")
            return
        if len(matches) > 1:
            names = ", ".join(td.name for _, td in matches[:8])
            await message.answer(t("st.ambiguous", lang, names=escape_html(names)), parse_mode="HTML")
            return
        code, td = matches[0]
        user.active_title_code = code
        await session.commit()
        await message.answer(
            t("st.set", lang, name=escape_html(td.name), rarity=td.rarity_label),
            parse_mode="HTML")


__all__ = ["router"]
