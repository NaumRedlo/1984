"""Unified bot settings command (`sts`).

An inline-keyboard menu, designed to grow: the first section is replay Render
(toggles + cyclers that actually drive danser via UserRenderSettings). Add future
sections by adding a button on the home menu and a `st:<section>` callback.
"""

import json
from typing import Optional

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from config.settings import ADMIN_IDS
from db.database import get_db_session
from db.models.render_settings import UserRenderSettings
from db.models.title_progress import UserTitleProgress
from utils.i18n import t
from utils.logger import get_logger
from utils.formatting.text import escape_html
from utils.osu.resolve_user import get_registered_user, get_registered_identity_user
from utils.osu import render_client
from utils.osu import danser_renderer
from utils.language import get_language, set_language
from utils.titles import TITLE_REGISTRY
from bot.filters import TextTriggerFilter
from bot.handlers.dm_tenant import ensure_dm_tenant
from bot.handlers.profile.render import (
    _get_or_create_settings, get_render_skins, get_my_render_skins,
    do_delete_skin, do_rename_skin, get_user_renders, get_user_render,
    delete_user_render, run_guarded_render, render_gate,
)

logger = get_logger("handlers.settings")
router = Router(name="settings")


class SkinManageStates(StatesGroup):
    """A single free-text step: waiting for the new name after tapping Rename
    on one of the user's own skins in My skins (see below)."""
    waiting_new_name = State()


# Owner-binding: a settings menu (and its callbacks) belongs to the user who
# opened it. In a group the message is visible to everyone, so without this a
# bystander could tap your buttons and drive (and mutate) settings on your card.
# Maps (chat_id, message_id) -> opener tg_id; checked by the guard below.
_MENU_OWNERS: dict = {}
_MENU_OWNERS_CAP = 2000


def _remember_owner(chat_id: int, message_id: int, tg_id: int) -> None:
    if len(_MENU_OWNERS) >= _MENU_OWNERS_CAP:
        # Drop the oldest ~half; menus are short-lived so this is cheap and rare.
        for k in list(_MENU_OWNERS)[: _MENU_OWNERS_CAP // 2]:
            _MENU_OWNERS.pop(k, None)
    _MENU_OWNERS[(chat_id, message_id)] = tg_id


def _is_foreign_menu_tap(data, chat_id, message_id, from_id) -> bool:
    """True if this is an `st:*` tap on a settings menu owned by someone else.
    Unknown owner (e.g. after a restart) returns False — each callback still
    resolves the caller's own data, so the worst case is cosmetic."""
    if not (data and data.startswith("st:")):
        return False
    owner = _MENU_OWNERS.get((chat_id, message_id))
    return owner is not None and owner != from_id


@router.callback_query.outer_middleware
async def _owner_guard(handler, event, data):
    """Block foreign taps on a settings menu (group chats — the message is visible
    to everyone). Also injects `lang` (the tapper's own language) for every
    callback handler on this router, so they don't each need their own
    get_language() call."""
    lang = (await get_language(event.from_user.id)).lower() if event.from_user else "en"
    data["lang"] = lang
    if isinstance(event, types.CallbackQuery) and event.message is not None:
        if _is_foreign_menu_tap(event.data, event.message.chat.id,
                                event.message.message_id, event.from_user.id):
            await event.answer(t("sts.foreign_menu", lang), show_alert=True)
            return
    return await handler(event, data)

# Boolean toggles: short code -> (model field, i18n key)
_TOGGLES = {
    "pp": ("show_pp_counter", "sts.toggle.pp"),
    "sb": ("show_scoreboard", "sts.toggle.sb"),
    "keys": ("show_key_overlay", "sts.toggle.keys"),
    "he": ("show_hit_error_meter", "sts.toggle.he"),
    "mods": ("show_mods", "sts.toggle.mods"),
    "rs": ("show_result_screen", "sts.toggle.rs"),
    "sg": ("show_strain_graph", "sts.toggle.sg"),
    "hc": ("show_hit_counter", "sts.toggle.hc"),
    "sc": ("show_score", "sts.toggle.sc"),
    "hp": ("show_hp_bar", "sts.toggle.hp"),
    "sw": ("show_seizure_warning", "sts.toggle.sw"),
    # ✅ = skin's own hitsounds, ❌ = the map's
    "hs": ("use_skin_hitsounds", "sts.toggle.hs"),
    # Master switch: hide the whole HUD (map + cursor only).
    "cin": ("cinema_mode", "sts.toggle.cin"),
}

_RES_CYCLE = ["1920x1080", "1280x720", "960x540"]
_DIM_CYCLE = [0, 20, 40, 60, 80, 100]
_CUR_CYCLE = [0.5, 0.8, 1.0, 1.2, 1.5, 2.0]
_VOL_CYCLE = [0, 25, 50, 75, 100]
_SKINS_PER_PAGE = 8


def _res_label(res: str) -> str:
    return {"1920x1080": "1080p", "1280x720": "720p", "960x540": "540p"}.get(res, res)


def _next(cycle, current):
    """Next value in a cycle, wrapping around (tolerant of an unknown current)."""
    try:
        return cycle[(cycle.index(current) + 1) % len(cycle)]
    except ValueError:
        return cycle[0]


def _home_kb(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("sts.kb.render", lang), callback_data="st:render")],
        [InlineKeyboardButton(text=t("sts.kb.my_renders", lang), callback_data="st:rnd")],
        [InlineKeyboardButton(text=t("sts.kb.account", lang), callback_data="st:acc")],
        [InlineKeyboardButton(text=t("sts.kb.title", lang), callback_data="st:tt")],
        [InlineKeyboardButton(text=t("sts.kb.language", lang), callback_data="st:lang")],
        [InlineKeyboardButton(text=t("sts.kb.close", lang), callback_data="st:close")],
    ])


def _nav_row(lang: str = "en") -> list:
    return [
        InlineKeyboardButton(text=t("sts.kb.back", lang), callback_data="st:home"),
        InlineKeyboardButton(text=t("sts.kb.close", lang), callback_data="st:close"),
    ]


# The render section is split into two screens: Video (output look) and
# Interface (the HUD toggles). hs (skin hitsounds) lives on the Video screen.
_VIDEO_TOGGLES = {"hs"}


def _toggle_btn(s, short: str, lang: str = "en") -> InlineKeyboardButton:
    field, key = _TOGGLES[short]
    on = getattr(s, field)
    return InlineKeyboardButton(
        text=f"{t(key, lang)}: {'✅' if on else '❌'}",
        callback_data=f"st:rt:{short}",
    )


def _render_back_row(lang: str = "en") -> list:
    return [
        InlineKeyboardButton(text=t("sts.kb.back", lang), callback_data="st:render"),
        InlineKeyboardButton(text=t("sts.kb.close", lang), callback_data="st:close"),
    ]


def _render_home_kb(lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("sts.kb.video", lang), callback_data="st:rvideo")],
        [InlineKeyboardButton(text=t("sts.kb.interface", lang), callback_data="st:rui")],
        [InlineKeyboardButton(text=t("sts.kb.reset_render", lang), callback_data="st:rreset")],
        _nav_row(lang),
    ])


def _video_kb(s, lang: str = "en") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("sts.kb.skin_label", lang, skin=s.skin), callback_data="st:rskin")],
        [InlineKeyboardButton(text=t("sts.kb.my_skins", lang), callback_data="st:myskins")],
        [InlineKeyboardButton(text=t("sts.kb.resolution", lang, value=_res_label(s.resolution)), callback_data="st:rc:res")],
        [InlineKeyboardButton(text=t("sts.kb.bg_dim", lang, value=s.bg_dim), callback_data="st:rc:dim")],
        [InlineKeyboardButton(text=t("sts.kb.cursor", lang, value=f"{s.cursor_size:g}"), callback_data="st:rc:cur")],
        [InlineKeyboardButton(text=t("sts.kb.music_vol", lang, value=s.music_volume), callback_data="st:rc:mus")],
        [InlineKeyboardButton(text=t("sts.kb.hitsound_vol", lang, value=s.hitsound_volume), callback_data="st:rc:hsv")],
        [_toggle_btn(s, "hs", lang)],
        _render_back_row(lang),
    ])


def _ui_kb(s, lang: str = "en") -> InlineKeyboardMarkup:
    # Cinema is a master switch (hides everything); when ON the toggles below are
    # overridden. One toggle per row (full width) — the labels are long.
    order = ["cin", "sc", "hp", "pp", "sb", "keys", "he", "mods", "rs", "sg", "hc", "sw"]
    rows = [[_toggle_btn(s, code, lang)] for code in order]
    rows.append(_render_back_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(TextTriggerFilter("sts"))
async def cmd_settings(message: types.Message, trigger_args=None, osu_api_client=None, tenant_chat_id=None):
    if not await ensure_dm_tenant(message, tenant_chat_id):
        return
    lang = (await get_language(message.from_user.id)).lower() if message.from_user else "en"
    sent = await message.answer(t("sts.home", lang), reply_markup=_home_kb(lang), parse_mode="HTML")
    # Bind this menu to its opener so bystanders can't drive it (group chats).
    _remember_owner(sent.chat.id, sent.message_id, message.from_user.id)


@router.callback_query(F.data == "st:home")
async def cb_home(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    try:
        await callback.message.edit_text(t("sts.home", lang), reply_markup=_home_kb(lang), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "st:close")
async def cb_close(callback: types.CallbackQuery, tenant_chat_id=None):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


async def _load_settings(callback: types.CallbackQuery, tenant_chat_id, lang: str = "en"):
    """Resolve the caller's render settings (or None + alert if not registered).
    The instance stays usable after the session closes — attributes are loaded."""
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return None
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        if not user:
            await callback.answer(t("sts.not_registered", lang), show_alert=True)
            return None
        return await _get_or_create_settings(session, user.id)


@router.callback_query(F.data == "st:render")
async def cb_render(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    try:
        await callback.message.edit_text(t("sts.render_home", lang), reply_markup=_render_home_kb(lang), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "st:rvideo")
async def cb_render_video(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    s = await _load_settings(callback, tenant_chat_id, lang)
    if s is None:
        return
    try:
        await callback.message.edit_text(t("sts.video_home", lang), reply_markup=_video_kb(s, lang), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "st:rui")
async def cb_render_ui(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    s = await _load_settings(callback, tenant_chat_id, lang)
    if s is None:
        return
    try:
        await callback.message.edit_text(t("sts.ui_home", lang), reply_markup=_ui_kb(s, lang), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


async def _mutate(callback: types.CallbackQuery, tenant_chat_id, apply_fn, kb_fn, lang: str = "en"):
    """Apply apply_fn(settings) for the caller, persist, and refresh the given
    sub-screen keyboard (kb_fn) in place."""
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        if not user:
            await callback.answer(t("sts.not_registered", lang), show_alert=True)
            return
        s = await _get_or_create_settings(session, user.id)
        apply_fn(s)
        await session.commit()
        await session.refresh(s)
        kb = kb_fn(s, lang)
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("st:rt:"))
async def cb_toggle(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    short = callback.data.split(":", 2)[2]
    entry = _TOGGLES.get(short)
    if not entry:
        await callback.answer()
        return
    field = entry[0]

    def apply(s):
        setattr(s, field, not getattr(s, field))

    kb_fn = _video_kb if short in _VIDEO_TOGGLES else _ui_kb
    await _mutate(callback, tenant_chat_id, apply, kb_fn, lang)


@router.callback_query(F.data.startswith("st:rc:"))
async def cb_cycle(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    which = callback.data.split(":", 2)[2]

    def apply(s):
        if which == "res":
            s.resolution = _next(_RES_CYCLE, s.resolution)
        elif which == "dim":
            s.bg_dim = _next(_DIM_CYCLE, s.bg_dim)
        elif which == "cur":
            s.cursor_size = _next(_CUR_CYCLE, s.cursor_size)
        elif which == "mus":
            s.music_volume = _next(_VOL_CYCLE, s.music_volume)
        elif which == "hsv":
            s.hitsound_volume = _next(_VOL_CYCLE, s.hitsound_volume)

    await _mutate(callback, tenant_chat_id, apply, _video_kb, lang)


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


@router.callback_query(F.data == "st:rreset")
async def cb_render_reset(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    """Reset render settings to defaults by dropping the row and recreating it."""
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        if not user:
            await callback.answer(t("sts.not_registered", lang), show_alert=True)
            return
        existing = (await session.execute(
            select(UserRenderSettings).where(UserRenderSettings.user_id == user.id)
        )).scalar_one_or_none()
        if existing:
            await session.delete(existing)
            await session.commit()
        await _get_or_create_settings(session, user.id)
    try:
        await callback.message.edit_text(t("sts.render_home", lang), reply_markup=_render_home_kb(lang), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer(t("sts.render_reset_done", lang))


# ── Account section (osu! link / relink / unlink) ──────────────────────────

async def _account_view(tg_id: int, lang: str = "en"):
    """Build (text, keyboard) for the Account section from the caller's global
    identity (OAuth is per Telegram id, not per group)."""
    from services.oauth.token_manager import has_oauth
    async with get_db_session() as session:
        user = await get_registered_identity_user(session, tg_id)
        linked = bool(user and user.osu_user_id)
        name = user.osu_username if user else None
    oauth = await has_oauth(tg_id) if linked else False

    if not linked:
        text = t("sts.acc.not_linked", lang)
        return text, InlineKeyboardMarkup(inline_keyboard=[_nav_row(lang)])

    oauth_status = t("sts.acc.oauth_yes" if oauth else "sts.acc.oauth_no", lang)
    text = t("sts.acc.linked", lang, name=escape_html(name), status=oauth_status)
    rows = []
    if oauth:
        rows.append([InlineKeyboardButton(text=t("sts.kb.relink", lang), callback_data="st:acc:relink")])
    else:
        rows.append([InlineKeyboardButton(text=t("sts.kb.link", lang), callback_data="st:acc:link")])
    rows.append([InlineKeyboardButton(text=t("sts.kb.unlink", lang), callback_data="st:acc:unlink")])
    rows.append(_nav_row(lang))
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "st:acc")
async def cb_account(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    text, kb = await _account_view(callback.from_user.id, lang)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


# ── Language (card text — EN/RU) ────────────────────────────────────────────
# Global per Telegram identity, same as Account/OAuth — not a per-chat setting,
# so no tenant_chat_id / ensure_dm_tenant involved.

def _language_kb(current: str, lang: str = "en") -> InlineKeyboardMarkup:
    def mark(code):
        return "● " if current == code else ""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{mark('EN')}🇬🇧 English", callback_data="st:lang:set:EN")],
        [InlineKeyboardButton(text=f"{mark('RU')}🇷🇺 Русский", callback_data="st:lang:set:RU")],
        _nav_row(lang),
    ])


@router.callback_query(F.data == "st:lang")
async def cb_language(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    current = await get_language(callback.from_user.id)
    text = t("sts.lang.view", lang, current=current)
    try:
        await callback.message.edit_text(text, reply_markup=_language_kb(current, lang), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("st:lang:set:"))
async def cb_language_set(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    new_card_lang = callback.data.split(":", 3)[3]
    if new_card_lang not in ("EN", "RU"):
        await callback.answer()
        return
    await set_language(callback.from_user.id, new_card_lang)
    # The Telegram UI itself follows the same setting, so re-render this
    # screen in the language just chosen rather than the stale injected one.
    ui_lang = new_card_lang.lower()
    try:
        await callback.message.edit_text(
            t("sts.lang.view", ui_lang, current=new_card_lang),
            reply_markup=_language_kb(new_card_lang, ui_lang), parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer(t("sts.lang.set_alert", ui_lang, lang=new_card_lang))


async def _send_oauth_link(callback: types.CallbackQuery, relink: bool, lang: str = "en"):
    """Send a fresh OAuth authorization link as a new message. For relink, drop
    the stored token first so a clean re-authorization is possible."""
    from services.oauth.server import generate_oauth_url, track_link_message
    tg_id = callback.from_user.id
    if relink:
        from sqlalchemy import delete
        from db.models.oauth_token import OAuthToken
        async with get_db_session() as session:
            await session.execute(delete(OAuthToken).where(OAuthToken.telegram_id == tg_id))
            await session.commit()
    url = generate_oauth_url(tg_id)
    title = t("sts.acc.relink_title" if relink else "sts.acc.link_title", lang)
    sent = await callback.message.answer(
        t("sts.acc.oauth_prompt", lang, title=title, url=url),
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    track_link_message(tg_id, sent.chat.id, sent.message_id)
    await callback.answer(t("sts.acc.link_sent", lang))


@router.callback_query(F.data == "st:acc:link")
async def cb_account_link(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    await _send_oauth_link(callback, relink=False, lang=lang)


@router.callback_query(F.data == "st:acc:relink")
async def cb_account_relink(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    await _send_oauth_link(callback, relink=True, lang=lang)


@router.callback_query(F.data == "st:acc:unlink")
async def cb_account_unlink(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    # Destructive — confirm first.
    text = t("sts.acc.unlink_confirm", lang)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("sts.kb.confirm_unlink", lang), callback_data="st:acc:unlinkyes")],
        [InlineKeyboardButton(text=t("sts.kb.cancel_back", lang), callback_data="st:acc")],
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "st:acc:unlinkyes")
async def cb_account_unlink_confirm(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    from bot.handlers.auth.handlers import perform_unlink
    from utils.osu.resolve_user import get_identity_user
    tg_id = callback.from_user.id
    async with get_db_session() as session:
        user = await get_identity_user(session, tg_id)
        ok, err = await perform_unlink(session, user, tg_id, lang)
    if not ok:
        if err == "not_linked":
            await callback.answer(t("sts.acc.not_linked_alert", lang), show_alert=True)
        else:
            await callback.answer(t("sts.acc.unlink_cooldown", lang, remaining=err), show_alert=True)
        return
    try:
        await callback.message.edit_text(
            t("sts.acc.unlinked", lang),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=t("sts.kb.close", lang), callback_data="st:close")],
            ]),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer(t("sts.done", lang))


# ── Title section (pick the active title shown on /profile) ─────────────────

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


# ── My renders (replay library — instant re-send by file_id) ───────────────

_RENDERS_PER_PAGE = 5


def _fmt(v, suffix=""):
    return f"{v}{suffix}" if v not in (None, "", 0) else None


async def _resolve_uid(callback: types.CallbackQuery, tenant_chat_id, lang: str = "en"):
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return None
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        if not user:
            await callback.answer(t("sts.not_registered", lang), show_alert=True)
            return None
        return user.id


async def _renders_view(uid, page: int = 0, lang: str = "en"):
    rows = await get_user_renders(uid)
    text = t("sts.renders.header", lang)
    kb = []
    if not rows:
        text += t("sts.renders.empty", lang)
    else:
        total_pages = (len(rows) + _RENDERS_PER_PAGE - 1) // _RENDERS_PER_PAGE
        page = max(0, min(page, total_pages - 1))
        text += t("sts.total", lang, n=len(rows))
        if total_pages > 1:
            text += t("sts.page_suffix", lang, page=page + 1, total=total_pages)
        text += t("sts.renders.pick", lang)
        start = page * _RENDERS_PER_PAGE
        for r in rows[start:start + _RENDERS_PER_PAGE]:
            kb.append([InlineKeyboardButton(
                text=(r.label or t("sts.renders.fallback_label", lang))[:60], callback_data=f"st:rnd:v:{page}:{r.id}")])
        if total_pages > 1:
            nav = []
            if page > 0:
                nav.append(InlineKeyboardButton(text="‹", callback_data=f"st:rnd:pg:{page - 1}"))
            nav.append(InlineKeyboardButton(text=f"{page + 1}/{total_pages}", callback_data="st:rnd:nop"))
            if page < total_pages - 1:
                nav.append(InlineKeyboardButton(text="›", callback_data=f"st:rnd:pg:{page + 1}"))
            kb.append(nav)
    kb.append(_nav_row(lang))
    return text, InlineKeyboardMarkup(inline_keyboard=kb)


async def _show_renders_page(callback: types.CallbackQuery, tenant_chat_id, page: int, lang: str = "en"):
    uid = await _resolve_uid(callback, tenant_chat_id, lang)
    if uid is None:
        return
    text, kb = await _renders_view(uid, page, lang)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "st:rnd")
async def cb_renders(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    await _show_renders_page(callback, tenant_chat_id, 0, lang)


@router.callback_query(F.data == "st:rnd:nop")
async def cb_renders_nop(callback: types.CallbackQuery, tenant_chat_id=None):
    await callback.answer()


@router.callback_query(F.data.startswith("st:rnd:pg:"))
async def cb_renders_page(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    try:
        page = int(callback.data.split(":", 3)[3])
    except (ValueError, IndexError):
        page = 0
    await _show_renders_page(callback, tenant_chat_id, page, lang)


def _render_detail_text(r, lang: str = "en") -> str:
    try:
        meta = json.loads(r.meta) if r.meta else {}
    except Exception:
        meta = {}
    head = r.label or t("sts.renders.fallback_label", lang)
    lines = [f"📼 <b>{escape_html(head)}</b>"]
    sub = []
    if meta.get("version"):
        sub.append(f"[{escape_html(str(meta['version']))}]")
    if meta.get("stars"):
        try:
            sub.append(f"★{float(meta['stars']):.2f}")
        except (TypeError, ValueError):
            pass
    if sub:
        lines.append(" ".join(sub))
    lines.append("")
    detail = [
        (t("sts.field.player", lang), _fmt(meta.get("player"))),
        (t("sts.field.mods", lang), _fmt(meta.get("mods"))),
        (t("sts.field.rank", lang), _fmt(meta.get("rank"))),
        (t("sts.field.pp", lang), _fmt(meta.get("pp"))),
        (t("sts.field.accuracy", lang), _fmt(f"{meta['acc']:.2f}", "%") if isinstance(meta.get("acc"), (int, float)) else None),
        (t("sts.field.combo", lang), _fmt(meta.get("combo"), "x")),
        (t("sts.field.misses", lang), _fmt(meta.get("misses"))),
    ]
    for label, val in detail:
        if val is not None:
            lines.append(f"{label}: <b>{escape_html(str(val))}</b>")
    if r.created_at:
        lines.append(t("sts.renders.rendered_at", lang, date=f"{r.created_at:%Y-%m-%d %H:%M}"))
    return "\n".join(lines)


@router.callback_query(F.data.startswith("st:rnd:v:"))
async def cb_render_detail(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    uid = await _resolve_uid(callback, tenant_chat_id, lang)
    if uid is None:
        return
    parts = callback.data.split(":", 4)  # st:rnd:v:<page>:<id>
    if len(parts) != 5:
        await callback.answer()
        return
    page = parts[3]
    try:
        render_id = int(parts[4])
    except ValueError:
        await callback.answer()
        return
    r = await get_user_render(uid, render_id)
    if not r:
        await callback.answer(t("sts.renders.not_found", lang), show_alert=True)
        await _show_renders_page(callback, tenant_chat_id, 0, lang)
        return
    kb = _render_detail_kb(r, page, lang)
    try:
        await callback.message.edit_text(_render_detail_text(r, lang), reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


def _render_detail_kb(r, page, lang: str = "en") -> InlineKeyboardMarkup:
    """A working render's detail screen: send / delete / back. (A BROKEN
    render — stale file_id — gets `_broken_view`'s screen instead, with a
    re-render option in place of "send".)"""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t("sts.kb.send_video", lang), callback_data=f"st:rnd:send:{r.id}")],
        [InlineKeyboardButton(text=t("sts.kb.delete", lang), callback_data=f"st:rnd:del:{r.id}")],
        [
            InlineKeyboardButton(text=t("sts.kb.back_to_list", lang), callback_data=f"st:rnd:pg:{page}"),
            InlineKeyboardButton(text=t("sts.kb.close", lang), callback_data="st:close"),
        ],
    ])


def _broken_view(r, lang: str = "en"):
    """A 'broken replay' screen offering delete / re-render (re-render only when we
    can reconstruct the inputs — a score entry with a known beatmapset)."""
    can_rerender = False
    try:
        meta = json.loads(r.meta) if r.meta else {}
    except Exception:
        meta = {}
    if str(r.ref).startswith("score:") and meta.get("beatmapset_id"):
        can_rerender = True
    text = (
        t("sts.renders.broken_header", lang)
        + f"<b>{escape_html(r.label or t('sts.renders.fallback_label', lang))}</b>\n"
        + t("sts.renders.broken_body", lang)
    )
    rows = []
    if can_rerender:
        rows.append([InlineKeyboardButton(text=t("sts.kb.rerender", lang), callback_data=f"st:rnd:re:{r.id}")])
    rows.append([InlineKeyboardButton(text=t("sts.kb.delete", lang), callback_data=f"st:rnd:del:{r.id}")])
    rows.append([InlineKeyboardButton(text=t("sts.kb.back_to_list", lang), callback_data="st:rnd:pg:0")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("st:rnd:send:"))
async def cb_render_send(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    uid = await _resolve_uid(callback, tenant_chat_id, lang)
    if uid is None:
        return
    try:
        render_id = int(callback.data.split(":", 3)[3])
    except (ValueError, IndexError):
        await callback.answer()
        return
    r = await get_user_render(uid, render_id)
    if not r:
        await callback.answer(t("sts.renders.not_found", lang), show_alert=True)
        return
    try:
        await callback.message.answer_video(video=r.file_id, supports_streaming=True)
        await callback.answer(t("sts.renders.sent", lang))
    except Exception as e:
        # Stale/broken file_id — surface a choice instead of a dead end.
        logger.info(f"render library re-send failed: {e}")
        await callback.answer(t("sts.renders.unavailable", lang), show_alert=True)
        text, kb = _broken_view(r, lang)
        try:
            await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        except Exception:
            pass


@router.callback_query(F.data.startswith("st:rnd:del:"))
async def cb_render_delete(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    uid = await _resolve_uid(callback, tenant_chat_id, lang)
    if uid is None:
        return
    try:
        render_id = int(callback.data.split(":", 3)[3])
    except (ValueError, IndexError):
        await callback.answer()
        return
    await delete_user_render(uid, render_id)
    await _show_renders_page(callback, tenant_chat_id, 0, lang)
    await callback.answer(t("sts.renders.deleted", lang))


@router.callback_query(F.data.startswith("st:rnd:re:"))
async def cb_render_rerender(callback: types.CallbackQuery, osu_api_client=None, tenant_chat_id=None, lang: str = "en"):
    uid = await _resolve_uid(callback, tenant_chat_id, lang)
    if uid is None:
        return
    try:
        render_id = int(callback.data.split(":", 3)[3])
    except (ValueError, IndexError):
        await callback.answer()
        return
    r = await get_user_render(uid, render_id)
    if not r or not str(r.ref).startswith("score:"):
        await callback.answer(t("sts.renders.rerender_unavailable", lang), show_alert=True)
        return
    try:
        meta = json.loads(r.meta) if r.meta else {}
    except Exception:
        meta = {}
    beatmapset_id = meta.get("beatmapset_id")
    if not beatmapset_id:
        await callback.answer(t("sts.renders.rerender_missing_data", lang), show_alert=True)
        return
    try:
        score_id = int(str(r.ref).split(":", 1)[1])
    except ValueError:
        await callback.answer()
        return

    tg_id = callback.from_user.id
    gate = render_gate(tg_id)
    if gate == "busy":
        await callback.answer(t("render.busy", lang), show_alert=True)
        return
    if gate and gate.startswith("cooldown:"):
        await callback.answer(t("render.cooldown_short", lang, sec=gate.split(':')[1]), show_alert=True)
        return

    await callback.answer(t("sts.renders.rerender_started", lang))
    await run_guarded_render(
        callback.message, score_id=score_id, beatmapset_id=beatmapset_id,
        display_name=meta.get("player") or "", length_seconds=meta.get("length"),
        meta=meta, tg_id=tg_id, tenant_chat_id=tenant_chat_id, osu_api_client=osu_api_client,
    )


__all__ = ["router"]
