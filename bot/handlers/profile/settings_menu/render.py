from dataclasses import replace

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_db_session
from utils.i18n import t
from utils.osu.resolve_user import get_registered_user
from bot.handlers.dossier import renders
from bot.handlers.profile.settings_menu import effects, skins, sound
from bot.handlers.profile.settings_menu import typed
from bot.handlers.profile.settings_menu.common import (
    _load, _nav_row, _store, sub_nav_row, switch_row,
)
from dossier import skins as skin_store

router = Router(name="settings_render")

OPTIONS: dict[str, list[list[tuple[str, str]]]] = {
    "size": [
        [("854x480", "480p"), ("1280x720", "720p"), ("1920x1080", "1080p")],
        [("2560x1440", "1440p"), ("3840x2160", "4K")],
    ],
    "fps": [[("30", "30 fps"), ("60", "60 fps"), ("120", "120 fps")]],
}

TOGGLES: tuple[str, ...] = ("mute", "background", "bare", "map_hitsounds", "leaderboard")

def _values(key: str) -> set[str]:
    return {value for row in OPTIONS.get(key, []) for value, _ in row}

def _current(choices: renders.Choices, key: str) -> str:
    if key in TOGGLES:
        return "1" if getattr(choices, key) else "0"
    return str(getattr(choices, key))

def _apply(choices: renders.Choices, key: str, value: str) -> bool:
    if key in TOGGLES:
        if value not in ("0", "1"):
            return False
        setattr(choices, key, value == "1")
        return True
    if value not in _values(key):
        return False
    if key == "fps":
        choices.fps = int(value)
    else:
        choices.size = value
    return True

def _option_rows(choices: renders.Choices) -> list:
    return [
        [
            InlineKeyboardButton(

                text=f"{'● ' if value == _current(choices, key) else ''}{shown}",
                callback_data=f"st:rnd:{key}:{value}",
            )
            for value, shown in row
        ]
        for key in OPTIONS
        for row in OPTIONS[key]
    ]

def _quality_kb(choices: renders.Choices, lang: str = "en") -> InlineKeyboardMarkup:

    rows = [
        [typed.value_button(choices, "size", lang)],
        [typed.value_button(choices, "fps", lang)],
    ]

    rows.append(switch_row(choices, ("background", "bare"), lang))
    rows.append(switch_row(choices, ("leaderboard",), lang))

    if choices.background:
        rows.append([typed.value_button(choices, "dim", lang)])
        rows.append([typed.value_button(choices, "blur", lang)])

    rows.append([typed.value_button(choices, "meter", lang)])
    rows.append([typed.value_button(choices, "cursor", lang)])
    rows.append(sub_nav_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _render_kb(
    choices: renders.Choices, sharing: bool, lang: str = "en"
) -> InlineKeyboardMarkup:

    rows = [
        [
            InlineKeyboardButton(text=t("sts.qly.tab", lang), callback_data="st:qly"),
            sound.tab_button(lang),
        ],
        [effects.tab_button(lang), skins.tab_button(lang)],
    ]

    rows.append([
        InlineKeyboardButton(
            text=f"{'☑️' if sharing else '⬜️'} {t('sts.rnd.share', lang)}",
            callback_data=f"st:rnd:share:{'0' if sharing else '1'}",
        )
    ])
    rows.append(_nav_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def _ration(tg_id: int, tenant_chat_id) -> int:
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id, tenant_chat_id)
        return renders.heavy_left(user)

async def _sharing(tg_id: int, tenant_chat_id) -> bool:
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id, tenant_chat_id)
        return bool(user and user.share_replays)

async def _set_sharing(tg_id: int, tenant_chat_id, on: bool) -> bool:
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id, tenant_chat_id)
        if not user:
            return False
        user.share_replays = on
        await session.commit()
        return True

def _text(choices: renders.Choices, sharing: bool, lang: str) -> str:
    body = t("sts.rnd.body", lang, summary=choices.summary(lang))
    if sharing:

        body += "\n\n" + t("sts.rnd.share_on", lang)
    return body

async def _show(callback: types.CallbackQuery, tenant_chat_id, lang: str) -> None:
    choices = renders.choices(callback.from_user.id)
    sharing = await _sharing(callback.from_user.id, tenant_chat_id)
    try:
        await callback.message.edit_text(
            _text(choices, sharing, lang),
            parse_mode="HTML",
            reply_markup=_render_kb(choices, sharing, lang),
        )
    except Exception:
        pass

def _quality_text(choices: renders.Choices, lang: str, left: int) -> str:

    return t("sts.qly.body", lang, summary=choices.summary(lang)) + "\n" + t(
        "sts.rnd.ration", lang, left=left, total=renders.HEAVY_PER_DAY
    )

async def _show_quality(callback: types.CallbackQuery, tenant_chat_id, lang: str) -> None:
    choices = renders.choices(callback.from_user.id)
    left = await _ration(callback.from_user.id, tenant_chat_id)
    try:
        await callback.message.edit_text(
            _quality_text(choices, lang, left),
            parse_mode="HTML",
            reply_markup=_quality_kb(choices, lang),
        )
    except Exception:
        pass

@router.callback_query(F.data == "st:qly")
async def cb_quality(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    await callback.answer()
    await _show_quality(callback, tenant_chat_id, lang)

@router.callback_query(F.data == "st:rnd")
async def cb_render(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    await _load(callback.from_user.id, tenant_chat_id)
    await _show(callback, tenant_chat_id, lang)
    await callback.answer()

@router.callback_query(F.data == "st:rnd:noop")
async def cb_label(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    await callback.answer()

@router.callback_query(F.data.startswith("st:rnd:skin:"))
async def cb_skin(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    wanted = callback.data.split(":", 3)[3]
    if wanted != skins.DEFAULT_SKIN and not skin_store.folder_of(wanted):

        await callback.answer(t("sts.rnd.skin_gone", lang), show_alert=True)
        await skins.show(callback, await _load(callback.from_user.id, tenant_chat_id), lang)
        return
    choices = renders.choices(callback.from_user.id)
    choices.skin = None if wanted == skins.DEFAULT_SKIN else wanted
    await _store(callback.from_user.id, tenant_chat_id, choices)
    await callback.answer(wanted)

    await skins.show(callback, choices, lang)

@router.callback_query(F.data.startswith("st:rnd:share:"))
async def cb_share(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    wanted = callback.data.rsplit(":", 1)[1] == "1"
    if not await _set_sharing(callback.from_user.id, tenant_chat_id, wanted):
        await callback.answer(t("sts.rnd.share_needs_account", lang), show_alert=True)
        return

    await callback.answer(
        t("sts.rnd.share_agreed" if wanted else "sts.rnd.share_off", lang),
        show_alert=wanted,
    )
    await _show(callback, tenant_chat_id, lang)

async def rationed(tg_id: int, tenant_chat_id, before, after, lang: str) -> str | None:
    if not after.heavy() or before.heavy():
        return None
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id, tenant_chat_id)
        if user is None:
            return t("sts.rnd.ration_needs_account", lang)
        if renders.heavy_left(user) <= 0:
            return t("sts.rnd.ration_spent", lang)
    return None

@router.callback_query(F.data.startswith("st:rnd:"))
async def cb_set(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    choices = renders.choices(callback.from_user.id)

    wanted = replace(choices)
    if not _apply(wanted, parts[2], parts[3]):
        await callback.answer(t("sts.rnd.unknown", lang), show_alert=True)
        return
    refusal = await rationed(callback.from_user.id, tenant_chat_id, choices, wanted, lang)
    if refusal:
        await callback.answer(refusal, show_alert=True)
        return
    _apply(choices, parts[2], parts[3])
    await _store(callback.from_user.id, tenant_chat_id, choices)
    await callback.answer(choices.summary(lang))

    if parts[2] in ("mute", "map_hitsounds"):
        await sound.show(callback, choices, lang)
    elif parts[2] in OPTIONS or parts[2] in ("background", "bare"):
        await _show_quality(callback, tenant_chat_id, lang)
    else:
        await _show(callback, tenant_chat_id, lang)
