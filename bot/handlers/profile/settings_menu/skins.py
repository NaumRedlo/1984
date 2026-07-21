"""Skin section. Two screens:

* Skin picker (`st:rskin`): select-only list of all selectable skins (built-in
  default + every uploaded one), for everyone.
* My skins (`st:myskins`): rename/delete the skins YOU uploaded. Admins see and
  manage EVERY skin here (incl. ownerless legacy ones), for cleanup.
"""

from typing import Optional

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config.settings import ADMIN_IDS
from db.database import get_db_session
from utils.i18n import t
from utils.formatting.text import escape_html
from utils.language import get_language
from utils.osu.resolve_user import get_registered_user
from utils.osu import render_client
from utils.osu import danser_renderer
from bot.handlers.profile.render import (
    _get_or_create_settings, get_render_skins, get_my_render_skins,
    do_delete_skin, do_rename_skin,
)
from bot.handlers.profile.settings_menu.common import _load_settings, _render_back_row

router = Router(name="settings_skins")

_SKINS_PER_PAGE = 8


class SkinManageStates(StatesGroup):
    """A single free-text step: waiting for the new name after tapping Rename
    on one of the user's own skins in My skins (see below)."""
    waiting_new_name = State()


# ── Skin picker (list, not a cycler) ──

async def _skin_list() -> list:
    """All selectable skins: the built-in default first, then uploaded ones.
    Select-only — no rename/delete here, regardless of who uploaded what (that
    lives in My skins, see below)."""
    return ["default"] + [e["name"] for e in await get_render_skins() if e["name"] != "default"]


def _skin_kb(skins, current, page: int, lang: str = "en"):
    total_pages = max(1, (len(skins) + _SKINS_PER_PAGE - 1) // _SKINS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    text = t("sts.skin.header", lang, current=escape_html(current))
    if total_pages > 1:
        text += t("sts.page_prefix", lang, page=page + 1, total=total_pages)
    text += t("sts.skin.pick", lang)
    rows = []
    start = page * _SKINS_PER_PAGE
    # Reference skins by INDEX (callback_data has a 64-byte cap; names can be long).
    for i in range(start, min(start + _SKINS_PER_PAGE, len(skins))):
        mark = "★ " if skins[i] == current else ""
        rows.append([InlineKeyboardButton(
            text=f"{mark}{skins[i]}"[:60], callback_data=f"st:rskin:set:{page}:{i}")])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="‹", callback_data=f"st:rskin:pg:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="st:rskin:nop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="›", callback_data=f"st:rskin:pg:{page + 1}"))
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(text=t("sts.kb.back_to_video", lang), callback_data="st:rvideo"),
        InlineKeyboardButton(text=t("sts.kb.close", lang), callback_data="st:close"),
    ])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_skin_page(callback: types.CallbackQuery, tenant_chat_id, page: int, lang: str = "en"):
    s = await _load_settings(callback, tenant_chat_id, lang)
    if s is None:
        return
    text, kb = _skin_kb(await _skin_list(), s.skin, page, lang)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass


@router.callback_query(F.data == "st:rskin")
async def cb_skin(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    await _show_skin_page(callback, tenant_chat_id, 0, lang)
    await callback.answer()


@router.callback_query(F.data == "st:rskin:nop")
async def cb_skin_nop(callback: types.CallbackQuery, tenant_chat_id=None):
    await callback.answer()


@router.callback_query(F.data.startswith("st:rskin:pg:"))
async def cb_skin_page(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    try:
        page = int(callback.data.split(":", 3)[3])
    except (ValueError, IndexError):
        page = 0
    await _show_skin_page(callback, tenant_chat_id, page, lang)
    await callback.answer()


@router.callback_query(F.data.startswith("st:rskin:set:"))
async def cb_skin_set(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    parts = callback.data.split(":", 4)  # st:rskin:set:<page>:<idx>
    if len(parts) != 5:
        await callback.answer()
        return
    try:
        page, idx = int(parts[3]), int(parts[4])
    except ValueError:
        await callback.answer()
        return
    skins = await _skin_list()
    if not (0 <= idx < len(skins)):
        await callback.answer(t("sts.skin.unavailable", lang), show_alert=True)
        return
    name = skins[idx]
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        if not user:
            await callback.answer(t("sts.not_registered", lang), show_alert=True)
            return
        s = await _get_or_create_settings(session, user.id)
        s.skin = name
        await session.commit()
    await _show_skin_page(callback, tenant_chat_id, page, lang)
    await callback.answer(t("sts.skin.selected", lang, name=name))


# ── My skins (only skins YOU uploaded — rename/delete live here, not in the
# general picker above, which stays select-only for everyone). Admins see and
# manage EVERY skin here (incl. ownerless legacy ones nobody else can reach),
# for cleanup — same screen, same actions, just an unfiltered list. ──

_MY_SKINS_PER_PAGE = 8


async def _manageable_skins(tg_id: int) -> list:
    """All skins if the caller is an admin (cleanup power over everything,
    including ownerless legacy entries); otherwise just their own uploads."""
    if tg_id in ADMIN_IDS:
        return await get_render_skins()
    return await get_my_render_skins(tg_id)


def _myskins_kb(skins: list, page: int, is_admin: bool = False, lang: str = "en") -> tuple:
    text = t("sts.myskins.header_admin" if is_admin else "sts.myskins.header", lang)
    if not skins:
        text += t("sts.myskins.empty", lang)
        return text, InlineKeyboardMarkup(inline_keyboard=[_render_back_row(lang)])

    total_pages = max(1, (len(skins) + _MY_SKINS_PER_PAGE - 1) // _MY_SKINS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))
    text += t("sts.total", lang, n=len(skins))
    if total_pages > 1:
        text += t("sts.page_suffix", lang, page=page + 1, total=total_pages)
    text += t("sts.myskins.pick", lang)
    rows = []
    start = page * _MY_SKINS_PER_PAGE
    for i in range(start, min(start + _MY_SKINS_PER_PAGE, len(skins))):
        rows.append([InlineKeyboardButton(
            text=skins[i]["name"][:60], callback_data=f"st:myskins:v:{page}:{i}")])
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(text="‹", callback_data=f"st:myskins:pg:{page - 1}"))
        nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="st:myskins:nop"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(text="›", callback_data=f"st:myskins:pg:{page + 1}"))
        rows.append(nav)
    rows.append(_render_back_row(lang))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_myskins_page(callback: types.CallbackQuery, page: int, lang: str = "en"):
    tg_id = callback.from_user.id
    skins = await _manageable_skins(tg_id)
    text, kb = _myskins_kb(skins, page, is_admin=tg_id in ADMIN_IDS, lang=lang)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "st:myskins")
async def cb_myskins(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    await _show_myskins_page(callback, 0, lang)


@router.callback_query(F.data == "st:myskins:nop")
async def cb_myskins_nop(callback: types.CallbackQuery, tenant_chat_id=None):
    await callback.answer()


@router.callback_query(F.data.startswith("st:myskins:pg:"))
async def cb_myskins_page(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    try:
        page = int(callback.data.split(":", 3)[3])
    except (ValueError, IndexError):
        page = 0
    await _show_myskins_page(callback, page, lang)


def _myskins_detail_kb(page: int, idx: int, lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("sts.kb.select", lang), callback_data=f"st:myskins:sel:{page}:{idx}")],
        [InlineKeyboardButton(text=t("sts.kb.rename", lang), callback_data=f"st:myskins:ren:{page}:{idx}")],
        [InlineKeyboardButton(text=t("sts.kb.delete", lang), callback_data=f"st:myskins:del:{page}:{idx}")],
        [
            InlineKeyboardButton(text=t("sts.kb.back_to_list", lang), callback_data=f"st:myskins:pg:{page}"),
            InlineKeyboardButton(text=t("sts.kb.close", lang), callback_data="st:close"),
        ],
    ])


async def _resolve_my_skin(callback: types.CallbackQuery, idx: int, lang: str = "en") -> Optional[str]:
    """Re-fetch the caller's manageable skins fresh and resolve idx -> name.
    Names are never put in callback_data directly (Telegram's 64-byte cap; skin
    names can run up to 64 chars themselves) — index into a freshly-fetched
    list, same convention as the general picker's st:rskin:set. This re-fetch
    also IS the authorization check: for a non-admin, an index only resolves
    against their OWN skins; admins resolve against every skin."""
    skins = await _manageable_skins(callback.from_user.id)
    if not (0 <= idx < len(skins)):
        await callback.answer(t("sts.skin.unavailable", lang), show_alert=True)
        return None
    return skins[idx]["name"]


@router.callback_query(F.data.startswith("st:myskins:v:"))
async def cb_myskins_detail(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    parts = callback.data.split(":", 4)  # st:myskins:v:<page>:<idx>
    if len(parts) != 5:
        await callback.answer()
        return
    try:
        page, idx = int(parts[3]), int(parts[4])
    except ValueError:
        await callback.answer()
        return
    name = await _resolve_my_skin(callback, idx, lang)
    if name is None:
        await _show_myskins_page(callback, 0, lang)
        return
    text = t("sts.myskins.detail", lang, name=escape_html(name))
    try:
        await callback.message.edit_text(
            text, reply_markup=_myskins_detail_kb(page, idx, lang), parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("st:myskins:sel:"))
async def cb_myskins_select(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    parts = callback.data.split(":", 4)  # st:myskins:sel:<page>:<idx>
    if len(parts) != 5:
        await callback.answer()
        return
    try:
        page, idx = int(parts[3]), int(parts[4])
    except ValueError:
        await callback.answer()
        return
    name = await _resolve_my_skin(callback, idx, lang)
    if name is None:
        return
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        if not user:
            await callback.answer(t("sts.not_registered", lang), show_alert=True)
            return
        s = await _get_or_create_settings(session, user.id)
        s.skin = name
        await session.commit()
    await callback.answer(t("sts.skin.selected", lang, name=name))
    await _show_myskins_page(callback, page, lang)


@router.callback_query(F.data.startswith("st:myskins:del:"))
async def cb_myskins_delete(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    parts = callback.data.split(":", 4)  # st:myskins:del:<page>:<idx>
    if len(parts) != 5:
        await callback.answer()
        return
    try:
        page, idx = int(parts[3]), int(parts[4])
    except ValueError:
        await callback.answer()
        return
    name = await _resolve_my_skin(callback, idx, lang)
    if name is None:
        return
    await callback.answer(t("sts.deleting", lang))
    try:
        await do_delete_skin(name)
    except render_client.RenderWorkerUnreachable:
        try:
            await callback.message.edit_text(t("render.worker_unreachable", lang))
        except Exception:
            pass
        return
    except danser_renderer.DanserError as e:
        try:
            await callback.message.edit_text(t("sts.skin.delete_error", lang, error=escape_html(str(e))), parse_mode="HTML")
        except Exception:
            pass
        return
    await _show_myskins_page(callback, page, lang)


@router.callback_query(F.data.startswith("st:myskins:ren:"))
async def cb_myskins_rename_start(callback: types.CallbackQuery, tenant_chat_id=None, state: FSMContext = None, lang: str = "en"):
    parts = callback.data.split(":", 4)  # st:myskins:ren:<page>:<idx>
    if len(parts) != 5:
        await callback.answer()
        return
    try:
        page, idx = int(parts[3]), int(parts[4])
    except ValueError:
        await callback.answer()
        return
    name = await _resolve_my_skin(callback, idx, lang)
    if name is None:
        return
    await state.update_data(skin_name=name)
    await state.set_state(SkinManageStates.waiting_new_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("sts.kb.cancel_back", lang), callback_data=f"st:myskins:rencancel:{page}")],
    ])
    try:
        await callback.message.edit_text(
            t("sts.skin.rename_prompt", lang, name=escape_html(name)),
            reply_markup=kb, parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("st:myskins:rencancel:"))
async def cb_myskins_rename_cancel(callback: types.CallbackQuery, tenant_chat_id=None, state: FSMContext = None, lang: str = "en"):
    # Must explicitly clear the state — otherwise the user's next unrelated
    # message would be swallowed by msg_myskins_rename_apply as a "new name".
    await state.clear()
    try:
        page = int(callback.data.split(":", 3)[3])
    except (ValueError, IndexError):
        page = 0
    await _show_myskins_page(callback, page, lang)


@router.message(SkinManageStates.waiting_new_name)
async def msg_myskins_rename_apply(message: types.Message, state: FSMContext, tenant_chat_id=None):
    lang = (await get_language(message.from_user.id)).lower() if message.from_user else "en"
    data = await state.get_data()
    name = data.get("skin_name")
    await state.clear()
    if not name:
        return
    new_name = (message.text or "").strip()
    if not new_name:
        await message.answer(t("sts.skin.empty_name", lang))
        return
    # Ownership (or admin status) could have changed since the prompt was shown — re-check.
    allowed = {e["name"] for e in await _manageable_skins(message.from_user.id)}
    if name not in allowed:
        await message.answer(t("sts.skin.not_yours", lang))
        return
    status = await message.answer(t("sts.renaming", lang), parse_mode="HTML")
    try:
        final_name = await do_rename_skin(name, new_name)
    except render_client.RenderWorkerUnreachable:
        await status.edit_text(t("render.worker_unreachable", lang))
        return
    except danser_renderer.DanserError as e:
        await status.edit_text(t("sts.skin.rename_error", lang, error=escape_html(str(e))), parse_mode="HTML")
        return
    await status.edit_text(t("sts.skin.renamed", lang, name=escape_html(final_name)), parse_mode="HTML")
