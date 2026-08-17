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

from dataclasses import replace

from aiogram import Router, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.database import get_db_session
from utils.i18n import t
from utils.osu.resolve_user import get_registered_user
from bot.handlers.dossier import renders
from bot.handlers.profile.settings_menu.common import _nav_row
from services.dossier import skins

router = Router(name="settings_render")

# The settings you pick one of, and how each reads. Kept as strings because that
# is what a callback carries, and parsed in one place.
#
# Laid out in rows here rather than in the keyboard: five resolutions in one row
# is six buttons wide once the label is counted, which Telegram renders as six
# unreadable slivers. The rows are the layout, and the layout belongs beside the
# values it is a layout for.
OPTIONS: dict[str, list[list[tuple[str, str]]]] = {
    "size": [
        [("854x480", "480p"), ("1280x720", "720p"), ("1920x1080", "1080p")],
        [("2560x1440", "1440p"), ("3840x2160", "4K")],
    ],
    "fps": [[("30", "30 fps"), ("60", "60 fps"), ("120", "120 fps")]],
}

# The settings that are simply on or off. One button apiece that offers the
# opposite of what is set, the way the consent box already worked — two buttons
# for a yes/no is twice the width to say the same thing.
TOGGLES: tuple[str, ...] = ("mute", "background", "bare")


def _values(key: str) -> set[str]:
    return {value for row in OPTIONS.get(key, []) for value, _ in row}


def _current(choices: renders.Choices, key: str) -> str:
    """What this setting is set to, as the callback data spells it."""
    if key in TOGGLES:
        return "1" if getattr(choices, key) else "0"
    return str(getattr(choices, key))


def _apply(choices: renders.Choices, key: str, value: str) -> bool:
    """Set one option. False when the pair is not one this menu offers — a
    callback is user input, and an old keyboard can outlive the option it was
    drawn for."""
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


# What the engine draws in when nobody has chosen a skin. Named here rather
# than spelled as a bare string in three places.
DEFAULT_SKIN = "classic"


def _skin_rows(choices: renders.Choices, lang: str) -> list:
    """The stored skins, three to a row, with the engine's own look first.

    Listed rather than typed: a skin arrives by sending the bot an `.osk`, and
    asking somebody to then remember its name would be a worse way to pick one
    than showing them. Three across because one per row turned a screen with a
    handful of skins into a scroll.
    """
    current = choices.skin or DEFAULT_SKIN
    buttons = []
    for name in [DEFAULT_SKIN, *skins.available()]:
        shown = t("sts.rnd.skin_default", lang) if name == DEFAULT_SKIN else name
        buttons.append(
            InlineKeyboardButton(
                text=f"{'● ' if name == current else ''}{shown}",
                # The name is checked against the store when it is used, so a
                # stale keyboard naming a deleted skin fails rather than
                # resolving to a path.
                callback_data=f"st:rnd:skin:{name}"[:64],
            )
        )
    return [buttons[at:at + 3] for at in range(0, len(buttons), 3)]


def _render_kb(
    choices: renders.Choices, sharing: bool, lang: str = "en", left: int = 0
) -> InlineKeyboardMarkup:
    rows = []
    for key in OPTIONS:
        for row in OPTIONS[key]:
            rows.append([
                InlineKeyboardButton(
                    # The chosen one is marked rather than hidden: a settings
                    # screen that shows only what you can change makes you tap
                    # something to find out what is already true.
                    text=f"{'● ' if value == _current(choices, key) else ''}{shown}",
                    callback_data=f"st:rnd:{key}:{value}",
                )
                for value, shown in row
            ])

    # The three switches on one row. Each says what it *is*, ticked or not,
    # rather than offering both halves of a yes/no as two buttons.
    rows.append([
        InlineKeyboardButton(
            text=f"{'☑️' if getattr(choices, key) else '⬜️'} {t(f'sts.rnd.{key}', lang)}",
            callback_data=f"st:rnd:{key}:{'0' if getattr(choices, key) else '1'}",
        )
        for key in TOGGLES
    ])

    rows.extend(_skin_rows(choices, lang))
    rows.append([
        InlineKeyboardButton(
            text=f"{'☑️' if sharing else '⬜️'} {t('sts.rnd.share', lang)}",
            callback_data=f"st:rnd:share:{'0' if sharing else '1'}",
        )
    ])
    rows.append(_nav_row(lang))
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ration(tg_id: int, tenant_chat_id) -> int:
    """How many renders above 1080p60 this person has left today."""
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id, tenant_chat_id)
        return renders.heavy_left(user)


async def _sharing(tg_id: int, tenant_chat_id) -> bool:
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id, tenant_chat_id)
        return bool(user and user.share_replays)


async def _load(tg_id: int, tenant_chat_id) -> renders.Choices:
    """This person's settings, from their row when they have one.

    Read on the way into the screen rather than held for ever: the bot may have
    restarted since they last set anything, and a settings screen showing
    defaults over stored values is worse than one that is slow to open.
    """
    choices = renders.choices(tg_id)
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id, tenant_chat_id)
        return renders.restore_settings(user, choices)


async def _store(tg_id: int, tenant_chat_id, choices: renders.Choices) -> None:
    async with get_db_session() as session:
        user = await get_registered_user(session, tg_id, tenant_chat_id)
        if user:
            renders.remember_settings(user, choices)
            await session.commit()


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


def _text(choices: renders.Choices, sharing: bool, lang: str, left: int = 0) -> str:
    body = t("sts.rnd.body", lang, summary=choices.summary())
    body += "\n" + t(
        "sts.rnd.ration", lang, left=left, total=renders.HEAVY_PER_DAY
    )
    if sharing:
        # Restated where it applies rather than only at the moment of turning it
        # on: this is the screen somebody opens months later wondering what the
        # bot has of theirs.
        body += "\n\n" + t("sts.rnd.share_on", lang)
    return body


async def _show(callback: types.CallbackQuery, tenant_chat_id, lang: str) -> None:
    choices = renders.choices(callback.from_user.id)
    sharing = await _sharing(callback.from_user.id, tenant_chat_id)
    left = await _ration(callback.from_user.id, tenant_chat_id)
    try:
        await callback.message.edit_text(
            _text(choices, sharing, lang, left),
            parse_mode="HTML",
            reply_markup=_render_kb(choices, sharing, lang, left),
        )
    except Exception:  # noqa: BLE001 — an unchanged message is not an error
        pass


@router.callback_query(F.data == "st:rnd")
async def cb_render(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    await _load(callback.from_user.id, tenant_chat_id)
    await _show(callback, tenant_chat_id, lang)
    await callback.answer()


@router.callback_query(F.data == "st:rnd:noop")
async def cb_label(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    """Row labels are buttons because Telegram has no other way to put text on a
    keyboard row. Tapping one does nothing, quietly."""
    await callback.answer()


@router.callback_query(F.data.startswith("st:rnd:skin:"))
async def cb_skin(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    wanted = callback.data.split(":", 3)[3]
    if wanted != DEFAULT_SKIN and not skins.folder_of(wanted):
        # The store is the authority, not the button: a keyboard outlives the
        # skin it was drawn for.
        await callback.answer(t("sts.rnd.skin_gone", lang), show_alert=True)
        await _show(callback, tenant_chat_id, lang)
        return
    choices = renders.choices(callback.from_user.id)
    choices.skin = None if wanted == DEFAULT_SKIN else wanted
    await _store(callback.from_user.id, tenant_chat_id, choices)
    await callback.answer(wanted)
    await _show(callback, tenant_chat_id, lang)


@router.callback_query(F.data.startswith("st:rnd:share:"))
async def cb_share(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    wanted = callback.data.rsplit(":", 1)[1] == "1"
    if not await _set_sharing(callback.from_user.id, tenant_chat_id, wanted):
        await callback.answer(t("sts.rnd.share_needs_account", lang), show_alert=True)
        return
    # Spelled out on the way in and not on the way out: agreeing is the half
    # worth being sure about. A short wording, because Telegram refuses a
    # callback answer over 200 characters outright — the long one is on the
    # screen below, which is where somebody looks months later anyway.
    await callback.answer(
        t("sts.rnd.share_agreed" if wanted else "sts.rnd.share_off", lang),
        show_alert=wanted,
    )
    await _show(callback, tenant_chat_id, lang)


@router.callback_query(F.data.startswith("st:rnd:"))
async def cb_set(callback: types.CallbackQuery, tenant_chat_id=None, lang: str = "en"):
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer()
        return
    choices = renders.choices(callback.from_user.id)
    # Applied to a copy first: a setting that turns out to be rationed must not
    # be left half-set, and "would this be rationed" is a question about the
    # result rather than about the button.
    wanted = replace(choices)
    if not _apply(wanted, parts[2], parts[3]):
        await callback.answer(t("sts.rnd.unknown", lang), show_alert=True)
        return
    # Refused here rather than when the video is asked for. Somebody who picked
    # 4K in the morning should not find out at midnight, holding a replay, that
    # the setting they chose was never going to run.
    if wanted.heavy() and not choices.heavy():
        async with get_db_session() as session:
            user = await get_registered_user(session, callback.from_user.id, tenant_chat_id)
            if user is None:
                await callback.answer(
                    t("sts.rnd.ration_needs_account", lang), show_alert=True
                )
                return
            if renders.heavy_left(user) <= 0:
                await callback.answer(t("sts.rnd.ration_spent", lang), show_alert=True)
                return
    _apply(choices, parts[2], parts[3])
    await _store(callback.from_user.id, tenant_chat_id, choices)
    await callback.answer(choices.summary())
    await _show(callback, tenant_chat_id, lang)
