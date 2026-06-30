"""Unified bot settings command (`settings` / `настройки`).

An inline-keyboard menu, designed to grow: the first section is replay Render
(toggles + cyclers that actually drive danser via UserRenderSettings). Add future
sections by adding a button on the home menu and a `st:<section>` callback.
"""

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_db_session
from utils.logger import get_logger
from utils.osu.resolve_user import get_registered_user
from bot.filters import TextTriggerFilter
from bot.handlers.dm_tenant import ensure_dm_tenant
from bot.handlers.profile.render import _get_or_create_settings, get_render_skins

logger = get_logger("handlers.settings")
router = Router(name="settings")

_HOME_TEXT = "⚙️ <b>Настройки</b>\n\nВыберите раздел:"
_RENDER_TEXT = "🎬 <b>Настройки рендера</b>\n\nНажмите параметр, чтобы изменить его:"

# Boolean toggles: short code -> (model field, label)
_TOGGLES = {
    "pp": ("show_pp_counter", "PP-счётчик"),
    "sb": ("show_scoreboard", "Скорборд"),
    "keys": ("show_key_overlay", "Клавиши"),
    "he": ("show_hit_error_meter", "Хит-ошибки"),
    "mods": ("show_mods", "Моды"),
    "rs": ("show_result_screen", "Экран результата"),
    "sg": ("show_strain_graph", "График сложности"),
    "hc": ("show_hit_counter", "Счётчик 300/100/50"),
    "sw": ("show_seizure_warning", "Эпилепсия-варнинг"),
    # ✅ = хитсаунды скина, ❌ = хитсаунды карты
    "hs": ("use_skin_hitsounds", "Хитсаунды скина"),
}

_RES_CYCLE = ["1920x1080", "1280x720", "960x540"]
_DIM_CYCLE = [0, 40, 80, 100]
_CUR_CYCLE = [0.8, 1.0, 1.2, 1.5]


def _res_label(res: str) -> str:
    return {"1920x1080": "1080p", "1280x720": "720p", "960x540": "540p"}.get(res, res)


def _next(cycle, current):
    """Next value in a cycle, wrapping around (tolerant of an unknown current)."""
    try:
        return cycle[(cycle.index(current) + 1) % len(cycle)]
    except ValueError:
        return cycle[0]


def _home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎬 Рендер реплеев", callback_data="st:render")],
        [InlineKeyboardButton(text="Закрыть", callback_data="st:close")],
    ])


def _render_kb(s) -> InlineKeyboardMarkup:
    def toggle_btn(short: str) -> InlineKeyboardButton:
        field, label = _TOGGLES[short]
        on = getattr(s, field)
        return InlineKeyboardButton(
            text=f"{label}: {'✅' if on else '❌'}",
            callback_data=f"st:rt:{short}",
        )

    rows = [
        [toggle_btn("pp"), toggle_btn("sb")],
        [toggle_btn("keys"), toggle_btn("he")],
        [toggle_btn("mods"), toggle_btn("rs")],
        [toggle_btn("sg"), toggle_btn("hc")],
        [toggle_btn("sw"), toggle_btn("hs")],
        [InlineKeyboardButton(text=f"Скин: {s.skin}", callback_data="st:rc:skin")],
        [InlineKeyboardButton(text=f"Разрешение: {_res_label(s.resolution)}", callback_data="st:rc:res")],
        [InlineKeyboardButton(text=f"Затемнение фона: {s.bg_dim}%", callback_data="st:rc:dim")],
        [InlineKeyboardButton(text=f"Курсор: {s.cursor_size:g}x", callback_data="st:rc:cur")],
        [
            InlineKeyboardButton(text="‹ Назад", callback_data="st:home"),
            InlineKeyboardButton(text="Закрыть", callback_data="st:close"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(TextTriggerFilter("settings", "настройки"))
async def cmd_settings(message: types.Message, trigger_args=None, osu_api_client=None, tenant_chat_id=None):
    if not await ensure_dm_tenant(message, tenant_chat_id):
        return
    await message.answer(_HOME_TEXT, reply_markup=_home_kb(), parse_mode="HTML")


@router.callback_query(F.data == "st:home")
async def cb_home(callback: types.CallbackQuery, tenant_chat_id=None):
    try:
        await callback.message.edit_text(_HOME_TEXT, reply_markup=_home_kb(), parse_mode="HTML")
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


async def _load_settings_kb(callback: types.CallbackQuery, tenant_chat_id):
    """Open the render section for the caller, returning its keyboard (or None if
    the user isn't registered — an alert is shown in that case)."""
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return None
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        if not user:
            await callback.answer("Вы не зарегистрированы. register [ник]", show_alert=True)
            return None
        s = await _get_or_create_settings(session, user.id)
        return _render_kb(s)


@router.callback_query(F.data == "st:render")
async def cb_render(callback: types.CallbackQuery, tenant_chat_id=None):
    kb = await _load_settings_kb(callback, tenant_chat_id)
    if kb is None:
        return
    try:
        await callback.message.edit_text(_RENDER_TEXT, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


async def _mutate(callback: types.CallbackQuery, tenant_chat_id, apply_fn):
    """Apply apply_fn(settings) for the caller, persist, and refresh the render
    keyboard in place."""
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        if not user:
            await callback.answer("Вы не зарегистрированы. register [ник]", show_alert=True)
            return
        s = await _get_or_create_settings(session, user.id)
        apply_fn(s)
        await session.commit()
        await session.refresh(s)
        kb = _render_kb(s)
    try:
        await callback.message.edit_reply_markup(reply_markup=kb)
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data.startswith("st:rt:"))
async def cb_toggle(callback: types.CallbackQuery, tenant_chat_id=None):
    short = callback.data.split(":", 2)[2]
    entry = _TOGGLES.get(short)
    if not entry:
        await callback.answer()
        return
    field = entry[0]

    def apply(s):
        setattr(s, field, not getattr(s, field))

    await _mutate(callback, tenant_chat_id, apply)


@router.callback_query(F.data.startswith("st:rc:"))
async def cb_cycle(callback: types.CallbackQuery, tenant_chat_id=None):
    which = callback.data.split(":", 2)[2]

    # Skin options come from the bot-side list (works even when the GPU is asleep);
    # always include the built-in "default".
    skin_cycle = ["default"]
    if which == "skin":
        skin_cycle += [n for n in await get_render_skins() if n != "default"]

    def apply(s):
        if which == "res":
            s.resolution = _next(_RES_CYCLE, s.resolution)
        elif which == "dim":
            s.bg_dim = _next(_DIM_CYCLE, s.bg_dim)
        elif which == "cur":
            s.cursor_size = _next(_CUR_CYCLE, s.cursor_size)
        elif which == "skin":
            s.skin = _next(skin_cycle, s.skin)

    await _mutate(callback, tenant_chat_id, apply)


__all__ = ["router"]
