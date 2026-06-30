"""Unified bot settings command (`sts`).

An inline-keyboard menu, designed to grow: the first section is replay Render
(toggles + cyclers that actually drive danser via UserRenderSettings). Add future
sections by adding a button on the home menu and a `st:<section>` callback.
"""

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from db.database import get_db_session
from db.models.render_settings import UserRenderSettings
from db.models.title_progress import UserTitleProgress
from utils.logger import get_logger
from utils.formatting.text import escape_html
from utils.osu.resolve_user import get_registered_user, get_registered_identity_user
from utils.titles import TITLE_REGISTRY
from bot.filters import TextTriggerFilter
from bot.handlers.dm_tenant import ensure_dm_tenant
from bot.handlers.profile.render import _get_or_create_settings, get_render_skins

logger = get_logger("handlers.settings")
router = Router(name="settings")

_HOME_TEXT = "⚙️ <b>Настройки</b>\n\nВыберите раздел:"
_RENDER_TEXT = "🎬 <b>Настройки рендера</b>\n\nВыберите категорию:"
_VIDEO_TEXT = "🎨 <b>Видео</b>\n\nНажмите параметр, чтобы изменить его:"
_UI_TEXT = "📊 <b>Интерфейс</b>\n\nНажмите элемент, чтобы вкл/выкл:"
_NOT_REGISTERED = "Вы не зарегистрированы. register [ник]"

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
        [InlineKeyboardButton(text="👤 Аккаунт", callback_data="st:acc")],
        [InlineKeyboardButton(text="🏅 Титул", callback_data="st:tt")],
        [InlineKeyboardButton(text="Закрыть", callback_data="st:close")],
    ])


def _nav_row() -> list:
    return [
        InlineKeyboardButton(text="‹ Назад", callback_data="st:home"),
        InlineKeyboardButton(text="Закрыть", callback_data="st:close"),
    ]


# The render section is split into two screens: Видео (output look) and
# Интерфейс (the HUD toggles). hs (skin hitsounds) lives on the Видео screen.
_VIDEO_TOGGLES = {"hs"}


def _toggle_btn(s, short: str) -> InlineKeyboardButton:
    field, label = _TOGGLES[short]
    on = getattr(s, field)
    return InlineKeyboardButton(
        text=f"{label}: {'✅' if on else '❌'}",
        callback_data=f"st:rt:{short}",
    )


def _render_back_row() -> list:
    return [
        InlineKeyboardButton(text="‹ Назад", callback_data="st:render"),
        InlineKeyboardButton(text="Закрыть", callback_data="st:close"),
    ]


def _render_home_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎨 Видео", callback_data="st:rvideo")],
        [InlineKeyboardButton(text="📊 Интерфейс", callback_data="st:rui")],
        [InlineKeyboardButton(text="↺ Сбросить настройки", callback_data="st:rreset")],
        _nav_row(),
    ])


def _video_kb(s) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Скин: {s.skin}", callback_data="st:rc:skin")],
        [InlineKeyboardButton(text=f"Разрешение: {_res_label(s.resolution)}", callback_data="st:rc:res")],
        [InlineKeyboardButton(text=f"Затемнение фона: {s.bg_dim}%", callback_data="st:rc:dim")],
        [InlineKeyboardButton(text=f"Курсор: {s.cursor_size:g}x", callback_data="st:rc:cur")],
        [_toggle_btn(s, "hs")],
        _render_back_row(),
    ])


def _ui_kb(s) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_toggle_btn(s, "pp"), _toggle_btn(s, "sb")],
        [_toggle_btn(s, "keys"), _toggle_btn(s, "he")],
        [_toggle_btn(s, "mods"), _toggle_btn(s, "rs")],
        [_toggle_btn(s, "sg"), _toggle_btn(s, "hc")],
        [_toggle_btn(s, "sw")],
        _render_back_row(),
    ])


@router.message(TextTriggerFilter("sts"))
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


async def _load_settings(callback: types.CallbackQuery, tenant_chat_id):
    """Resolve the caller's render settings (or None + alert if not registered).
    The instance stays usable after the session closes — attributes are loaded."""
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return None
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        if not user:
            await callback.answer(_NOT_REGISTERED, show_alert=True)
            return None
        return await _get_or_create_settings(session, user.id)


@router.callback_query(F.data == "st:render")
async def cb_render(callback: types.CallbackQuery, tenant_chat_id=None):
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    try:
        await callback.message.edit_text(_RENDER_TEXT, reply_markup=_render_home_kb(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "st:rvideo")
async def cb_render_video(callback: types.CallbackQuery, tenant_chat_id=None):
    s = await _load_settings(callback, tenant_chat_id)
    if s is None:
        return
    try:
        await callback.message.edit_text(_VIDEO_TEXT, reply_markup=_video_kb(s), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "st:rui")
async def cb_render_ui(callback: types.CallbackQuery, tenant_chat_id=None):
    s = await _load_settings(callback, tenant_chat_id)
    if s is None:
        return
    try:
        await callback.message.edit_text(_UI_TEXT, reply_markup=_ui_kb(s), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


async def _mutate(callback: types.CallbackQuery, tenant_chat_id, apply_fn, kb_fn):
    """Apply apply_fn(settings) for the caller, persist, and refresh the given
    sub-screen keyboard (kb_fn) in place."""
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        if not user:
            await callback.answer(_NOT_REGISTERED, show_alert=True)
            return
        s = await _get_or_create_settings(session, user.id)
        apply_fn(s)
        await session.commit()
        await session.refresh(s)
        kb = kb_fn(s)
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

    kb_fn = _video_kb if short in _VIDEO_TOGGLES else _ui_kb
    await _mutate(callback, tenant_chat_id, apply, kb_fn)


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

    await _mutate(callback, tenant_chat_id, apply, _video_kb)


@router.callback_query(F.data == "st:rreset")
async def cb_render_reset(callback: types.CallbackQuery, tenant_chat_id=None):
    """Reset render settings to defaults by dropping the row and recreating it."""
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        if not user:
            await callback.answer(_NOT_REGISTERED, show_alert=True)
            return
        existing = (await session.execute(
            select(UserRenderSettings).where(UserRenderSettings.user_id == user.id)
        )).scalar_one_or_none()
        if existing:
            await session.delete(existing)
            await session.commit()
        await _get_or_create_settings(session, user.id)
    try:
        await callback.message.edit_text(_RENDER_TEXT, reply_markup=_render_home_kb(), parse_mode="HTML")
    except Exception:
        pass
    await callback.answer("Настройки рендера сброшены ↺")


# ── Account section (osu! link / relink / unlink) ──────────────────────────

async def _account_view(tg_id: int):
    """Build (text, keyboard) for the Account section from the caller's global
    identity (OAuth is per Telegram id, not per group)."""
    from services.oauth.token_manager import has_oauth
    async with get_db_session() as session:
        user = await get_registered_identity_user(session, tg_id)
        linked = bool(user and user.osu_user_id)
        name = user.osu_username if user else None
    oauth = await has_oauth(tg_id) if linked else False

    if not linked:
        text = (
            "👤 <b>Аккаунт</b>\n\n"
            "osu! не привязан.\n"
            "Зарегистрируйтесь в беседе: <code>register [ник]</code>"
        )
        return text, InlineKeyboardMarkup(inline_keyboard=[_nav_row()])

    text = (
        "👤 <b>Аккаунт</b>\n\n"
        f"osu!: <b>{escape_html(name)}</b>\n"
        f"OAuth: {'✅ привязан' if oauth else '❌ не привязан'}"
    )
    rows = []
    if oauth:
        rows.append([InlineKeyboardButton(text="🔁 Перепривязать osu!", callback_data="st:acc:relink")])
    else:
        rows.append([InlineKeyboardButton(text="🔗 Привязать osu!", callback_data="st:acc:link")])
    rows.append([InlineKeyboardButton(text="❌ Отвязать аккаунт", callback_data="st:acc:unlink")])
    rows.append(_nav_row())
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "st:acc")
async def cb_account(callback: types.CallbackQuery, tenant_chat_id=None):
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    text, kb = await _account_view(callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


async def _send_oauth_link(callback: types.CallbackQuery, relink: bool):
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
    title = "🔁 Перепривязка osu!" if relink else "🔗 Привязка osu!"
    sent = await callback.message.answer(
        f"{title}\n\n"
        f"Откройте ссылку и авторизуйтесь:\n"
        f"<a href=\"{url}\">Авторизоваться в osu!</a>\n\n"
        f"После авторизации вернитесь в Telegram.",
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
    track_link_message(tg_id, sent.chat.id, sent.message_id)
    await callback.answer("Ссылка отправлена ниже ⬇️")


@router.callback_query(F.data == "st:acc:link")
async def cb_account_link(callback: types.CallbackQuery, tenant_chat_id=None):
    await _send_oauth_link(callback, relink=False)


@router.callback_query(F.data == "st:acc:relink")
async def cb_account_relink(callback: types.CallbackQuery, tenant_chat_id=None):
    await _send_oauth_link(callback, relink=True)


@router.callback_query(F.data == "st:acc:unlink")
async def cb_account_unlink(callback: types.CallbackQuery, tenant_chat_id=None):
    # Destructive — confirm first.
    text = (
        "⚠️ <b>Отвязать osu! аккаунт?</b>\n\n"
        "Будут удалены: привязка, OAuth, очки HPS, ранг, титулы и кэш скоров.\n"
        "Повторная отвязка доступна раз в месяц."
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Да, отвязать", callback_data="st:acc:unlinkyes")],
        [InlineKeyboardButton(text="‹ Отмена", callback_data="st:acc")],
    ])
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()


@router.callback_query(F.data == "st:acc:unlinkyes")
async def cb_account_unlink_confirm(callback: types.CallbackQuery, tenant_chat_id=None):
    from bot.handlers.auth.handlers import perform_unlink
    from utils.osu.resolve_user import get_identity_user
    tg_id = callback.from_user.id
    async with get_db_session() as session:
        user = await get_identity_user(session, tg_id)
        ok, err = await perform_unlink(session, user, tg_id)
    if not ok:
        if err == "not_linked":
            await callback.answer("Аккаунт не привязан.", show_alert=True)
        else:
            await callback.answer(f"Отвязка раз в месяц. Повторите через {err}.", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            "✅ Аккаунт osu! отвязан. Повторная отвязка доступна через месяц.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Закрыть", callback_data="st:close")],
            ]),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await callback.answer("Готово")


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


async def _title_view(tg_id: int, tenant_chat_id, page: int = 0):
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
    text = (
        "🏅 <b>Титул</b>\n\n"
        f"Активный: <b>{escape_html(active_name) if active_name else '— нет —'}</b>\n\n"
    )
    rows = []
    if not ordered:
        page = 0
        text += "Пока нет открытых титулов. Открывайте их игрой — <code>tt</code>."
    else:
        total_pages = (len(ordered) + _TITLES_PER_PAGE - 1) // _TITLES_PER_PAGE
        page = max(0, min(page, total_pages - 1))
        text += "Выберите титул для профиля:"
        if total_pages > 1:
            text += f"  (стр. {page + 1}/{total_pages})"
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
        rows.append([InlineKeyboardButton(text="Снять титул", callback_data=f"st:tt:off:{page}")])
    rows.append(_nav_row())
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_title_page(callback: types.CallbackQuery, tenant_chat_id, page: int):
    text, kb = await _title_view(callback.from_user.id, tenant_chat_id, page)
    if text is None:
        await callback.answer(_NOT_REGISTERED, show_alert=True)
        return False
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    return True


@router.callback_query(F.data == "st:tt")
async def cb_title(callback: types.CallbackQuery, tenant_chat_id=None):
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    await _show_title_page(callback, tenant_chat_id, 0)
    await callback.answer()


@router.callback_query(F.data == "st:tt:nop")
async def cb_title_nop(callback: types.CallbackQuery, tenant_chat_id=None):
    await callback.answer()


@router.callback_query(F.data.startswith("st:tt:pg:"))
async def cb_title_page(callback: types.CallbackQuery, tenant_chat_id=None):
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    try:
        page = int(callback.data.split(":", 3)[3])
    except (ValueError, IndexError):
        page = 0
    await _show_title_page(callback, tenant_chat_id, page)
    await callback.answer()


async def _set_active_title(callback: types.CallbackQuery, tenant_chat_id, code, page: int):
    """Persist active_title_code (validated unlocked, or None to clear) and refresh
    the same page."""
    async with get_db_session() as session:
        user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
        if not user:
            await callback.answer(_NOT_REGISTERED, show_alert=True)
            return
        if code is not None:
            codes = await _unlocked_title_codes(session, user.id)
            if code not in codes:
                await callback.answer("Этот титул ещё не открыт.", show_alert=True)
                return
        user.active_title_code = code
        await session.commit()
    await _show_title_page(callback, tenant_chat_id, page)
    if code is None:
        await callback.answer("Титул снят.")
    else:
        td = TITLE_REGISTRY.get(code)
        await callback.answer(f"★ {td.name if td else code}")


@router.callback_query(F.data.startswith("st:tt:set:"))
async def cb_title_set(callback: types.CallbackQuery, tenant_chat_id=None):
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
    await _set_active_title(callback, tenant_chat_id, parts[4], page)


@router.callback_query(F.data.startswith("st:tt:off:"))
async def cb_title_off(callback: types.CallbackQuery, tenant_chat_id=None):
    if not await ensure_dm_tenant(callback, tenant_chat_id):
        return
    try:
        page = int(callback.data.split(":", 3)[3])
    except (ValueError, IndexError):
        page = 0
    await _set_active_title(callback, tenant_chat_id, None, page)


__all__ = ["router"]
