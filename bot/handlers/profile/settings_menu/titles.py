"""Title section (`st:tt`): pick the active title shown on /profile from the
titles the caller has unlocked (or clear it)."""

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from db.database import get_db_session
from db.models.title_progress import UserTitleProgress
from utils.i18n import t
from utils.formatting.text import escape_html
from utils.osu.resolve_user import get_registered_user
from utils.titles import TITLE_REGISTRY
from bot.handlers.dm_tenant import ensure_dm_tenant
from bot.handlers.profile.settings_menu.common import _nav_row

router = Router(name="settings_titles")

_TITLES_PER_PAGE = 5


async def _unlocked_title_codes(session, user_id: int) -> set:
    rows = await session.execute(
        select(UserTitleProgress.title_code).where(
            UserTitleProgress.user_id == user_id,
            UserTitleProgress.unlocked == True,  # noqa: E712
        )
    )
    return {r[0] for r in rows.all()}


async def _title_view(tg_id: int, tenant_chat_id, page: int = 0, lang: str = "en"):
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id, tenant_chat_id)
        if not user:
            return None, None
        active = user.active_title_code
        codes = await _unlocked_title_codes(session, user.id)

    active_name = None
    if active:
        td = TITLE_REGISTRY.get(active)
        active_name = td.name if td else active

    # Registry order keeps titles grouped by rarity.
    ordered = [c for c in TITLE_REGISTRY if c in codes]
    text = t("sts.title.header", lang, name=escape_html(active_name) if active_name else t("sts.title.none", lang))
    rows = []
    if not ordered:
        page = 0
        text += t("sts.title.no_unlocked", lang)
    else:
        total_pages = (len(ordered) + _TITLES_PER_PAGE - 1) // _TITLES_PER_PAGE
        page = max(0, min(page, total_pages - 1))
        text += t("sts.title.pick", lang)
        if total_pages > 1:
            text += t("sts.page_suffix", lang, page=page + 1, total=total_pages)
        start = page * _TITLES_PER_PAGE
        for code in ordered[start:start + _TITLES_PER_PAGE]:
            td = TITLE_REGISTRY[code]
            mark = "★ " if code == active else ""
            rows.append([InlineKeyboardButton(
                text=f"{mark}{td.name}", callback_data=f"st:tt:set:{page}:{code}")])
        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton(text="‹", callback_data=f"st:tt:pg:{page - 1}"))
            nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="st:tt:nop"))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton(text="›", callback_data=f"st:tt:pg:{page + 1}"))
            rows.append(nav)
    if active:
        rows.append([InlineKeyboardButton(text=t("sts.kb.clear_title", lang), callback_data=f"st:tt:off:{page}")])
    rows.append(_nav_row(lang))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_title_page(callback: types.CallbackQuery, tenant_chat_id, page: int, lang: str = "en"):
    text, kb = await _title_view(callback.from_user.id, tenant_chat_id, page, lang)
    if text is None:
        await callback.answer(t("sts.not_registered", lang), show_alert=True)
        return False
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    return True


@router.callback_query(F.data == "st:tt")
async def cb_title(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    await _show_title_page(callback, tenant_chat_id, 0, lang)
    await callback.answer()


@router.callback_query(F.data == "st:tt:nop")
async def cb_title_nop(callback: types.CallbackQuery, tenant_chat_id=None):
    await callback.answer()


@router.callback_query(F.data.startswith("st:tt:pg:"))
async def cb_title_page(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    try:
        page = int(callback.data.split(":", 3)[3])
    except (ValueError, IndexError):
        page = 0
    await _show_title_page(callback, tenant_chat_id, page, lang)
    await callback.answer()


async def _set_active_title(callback: types.CallbackQuery, tenant_chat_id, code, page: int, lang: str = "en"):
    """Persist active_title_code (validated unlocked, or None to clear) and refresh
    the same page."""
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        if not user:
            await callback.answer(t("sts.not_registered", lang), show_alert=True)
            return
        if code is not None:
            codes = await _unlocked_title_codes(session, user.id)
            if code not in codes:
                await callback.answer(t("sts.title.not_unlocked", lang), show_alert=True)
                return
        user.active_title_code = code
        await session.commit()
    await _show_title_page(callback, tenant_chat_id, page, lang)
    if code is None:
        await callback.answer(t("st.cleared", lang))
    else:
        td = TITLE_REGISTRY.get(code)
        await callback.answer(t("sts.title.set_alert", lang, name=td.name if td else code))


@router.callback_query(F.data.startswith("st:tt:set:"))
async def cb_title_set(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    parts = callback.data.split(":", 4)  # st:tt:set:<page>:<code>
    if len(parts) != 5:
        await callback.answer()
        return
    try:
        page = int(parts[3])
    except ValueError:
        page = 0
    await _set_active_title(callback, tenant_chat_id, parts[4], page, lang)


@router.callback_query(F.data.startswith("st:tt:off:"))
async def cb_title_off(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    try:
        page = int(callback.data.split(":", 3)[3])
    except (ValueError, IndexError):
        page = 0
    await _set_active_title(callback, tenant_chat_id, None, page, lang)
