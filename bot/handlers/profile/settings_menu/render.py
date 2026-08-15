"""Render section (`st:rnd`): how a replay render is made, and who sees it.

These used to live on a keyboard hung off the replay itself, which meant the
settings only existed while a replay did: they were reachable after uploading a
file and nowhere else, and a question like "what will this render at" had no
answer until you had something to render. They belong with the rest of a
person's settings, so here they are.

Two different kinds of thing sit here, and they are stored differently on
purpose. Size, frame rate and sound are *preferences*, held in memory beside
the pending renders — losing them to a restart costs a tap. Sharing replays is
*permission*, and permission is written down: a restart may forget that someone
wanted 60fps and must not forget whether they agreed to hand over their files.

The fine control this will grow into does not exist yet. What is here is what
the engine already reads.
"""

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_db_session
from utils.i18n import t
from utils.osu.resolve_user import get_registered_user
from bot.handlers.dossier import renders
from bot.handlers.profile.settings_menu.common import _nav_row

router = Router(name="settings_render")

# Each row is one setting: the values it can take, and how each reads. Kept as
# strings because that is what a callback carries, and parsed in one place.
OPTIONS: dict[str, list[tuple[str, str]]] = {
    "size": [("854x480", "480p"), ("1280x720", "720p"), ("1920x1080", "1080p")],
    "fps": [("30", "30"), ("60", "60")],
    "mute": [("0", "sts.rnd.sound_on"), ("1", "sts.rnd.sound_off")],
}


def _current(choices: renders.Choices, key: str) -> str:
    """What this setting is set to, as the callback data spells it."""
    if key == "mute":
        return "1" if choices.mute else "0"
    return str(getattr(choices, key))


def _apply(choices: renders.Choices, key: str, value: str) -> bool:
    """Set one option. False when the pair is not one this menu offers — a
    callback is user input, and an old keyboard can outlive the option it was
    drawn for."""
    if key not in OPTIONS or value not in {v for v, _ in OPTIONS[key]}:
        return False
    if key == "mute":
        choices.mute = value == "1"
    elif key == "fps":
        choices.fps = int(value)
    else:
        choices.size = value
    return True


def _render_kb(choices: renders.Choices, sharing: bool, lang: str = "en") -> InlineKeyboardMarkup:
    rows = []
    for key, values in OPTIONS.items():
        rows.append(
            [
                InlineKeyboardButton(
                    # The chosen one is marked rather than hidden: a settings
                    # screen that shows only what you can change makes you tap
                    # something to find out what is already true.
                    text=f"{'● ' if value == _current(choices, key) else ''}"
                    f"{t(shown, lang) if shown.startswith('sts.') else shown}",
                    callback_data=f"st:rnd:{key}:{value}",
                )
                for value, shown in values
            ]
        )
        rows[-1].insert(
            0,
            InlineKeyboardButton(text=t(f"sts.rnd.{key}", lang), callback_data="st:rnd:noop"),
        )
    rows.append([
        InlineKeyboardButton(
            text=f"{'☑️' if sharing else '⬜️'} {t('sts.rnd.share', lang)}",
            callback_data=f"st:rnd:share:{'0' if sharing else '1'}",
        )
    ])
    rows.append(_nav_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _sharing(tg_id: int, tenant_chat_id) -> bool:
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id, tenant_chat_id)
        return bool(user and user.share_replays)


async def _set_sharing(tg_id: int, tenant_chat_id, on: bool) -> bool:
    """Returns whether it could be written — an unlinked account has nowhere to
    keep a permission, and saying so beats a toggle that silently forgets."""
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id, tenant_chat_id)
        if not user:
            return False
        user.share_replays = on
        await session.commit()
        return True


def _text(choices: renders.Choices, sharing: bool, lang: str) -> str:
    body = t("sts.rnd.body", lang, summary=choices.summary())
    if sharing:
        # Restated where it applies rather than only at the moment of turning it
        # on: this is the screen somebody opens months later wondering what the
        # bot has of theirs.
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
    except Exception:  # noqa: BLE001 — an unchanged message is not an error
        pass


@router.callback_query(F.data == "st:rnd")
async def cb_render(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    await _show(callback, tenant_chat_id, lang)
    await callback.answer()


@router.callback_query(F.data == "st:rnd:noop")
async def cb_label(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    """Row labels are buttons because Telegram has no other way to put text on a
    keyboard row. Tapping one does nothing, quietly."""
    await callback.answer()


@router.callback_query(F.data.startswith("st:rnd:share:"))
async def cb_share(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    wanted = callback.data.rsplit(":", 1)[1] == "1"
    if not await _set_sharing(callback.from_user.id, tenant_chat_id, wanted):
        await callback.answer(t("sts.rnd.share_needs_account", lang), show_alert=True)
        return
    # Spelled out on the way in and not on the way out: agreeing is the half
    # worth being sure about.
    await callback.answer(
        t("sts.rnd.share_on" if wanted else "sts.rnd.share_off", lang), show_alert=wanted
    )
    await _show(callback, tenant_chat_id, lang)


@router.callback_query(F.data.startswith("st:rnd:"))
async def cb_set(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    choices = renders.choices(callback.from_user.id)
    if not _apply(choices, parts[2], parts[3]):
        await callback.answer(t("sts.rnd.unknown", lang), show_alert=True)
        return
    await callback.answer(choices.summary())
    await _show(callback, tenant_chat_id, lang)
