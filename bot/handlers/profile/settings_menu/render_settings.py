"""Render section (`st:render`): the Video screen (output look — skin,
resolution, dim, cursor, volumes, skin hitsounds) and the Interface screen (HUD
toggles), plus reset-to-defaults. These drive danser via UserRenderSettings.
"""

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from db.database import get_db_session
from db.models.render_settings import UserRenderSettings
from utils.i18n import t
from bot.handlers.dm_tenant import ensure_dm_tenant
from bot.handlers.profile.render import _get_or_create_settings
from utils.osu.resolve_user import get_registered_user
from bot.handlers.profile.settings_menu.common import (
    _load_settings, _nav_row, _render_back_row,
)

router = Router(name="settings_render")


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


def _res_label(res: str) -> str:
    return {"1920x1080": "1080p", "1280x720": "720p", "960x540": "540p"}.get(res, res)


def _next(cycle, current):
    """Next value in a cycle, wrapping around (tolerant of an unknown current)."""
    try:
        return cycle[(cycle.index(current) + 1) % len(cycle)]
    except ValueError:
        return cycle[0]


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
