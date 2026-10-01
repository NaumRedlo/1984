from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)

from bot.filters import TextTriggerFilter, TriggerArgs
from services.render_farm import invites, members, pairing
from utils.formatting.text import escape_html
from utils.i18n import t
from utils.language import get_language
from utils.render_access import can_use_render

router = Router(name="start")

PAIR_PREFIX = "pair-"

async def _lang_of(user) -> str:
    return (await get_language(user.id)).lower() if user else "en"

async def _send_welcome(message: Message):
    lang = await _lang_of(message.from_user)
    name = escape_html(message.from_user.first_name or "")
    await message.answer(
        t("start.welcome", lang, sep="═" * 30, name=name),
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )

def _about(machine: pairing.Machine, lang: str) -> str:
    parts = [escape_html(machine.os)] if machine.os else []
    if machine.cores:
        parts.append(t("dsr.pair.cores", lang, n=machine.cores))
    if machine.build:
        parts.append(t("dsr.pair.build", lang, build=escape_html(machine.build)))
    return ", ".join(parts)

async def _may_pair(bot, telegram_id: int) -> bool:
    return can_use_render(telegram_id) or await members.shares_a_group(bot, telegram_id)

def _pair_keyboard(code: str, lang: str, yes: str = "dsr.pair.yes") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t(yes, lang), callback_data=f"pair:yes:{code}"),
        InlineKeyboardButton(text=t("dsr.pair.no", lang), callback_data=f"pair:no:{code}"),
    ]])

def _links_telegram(found) -> bool:
    return found is not None and found.wants_telegram_for is not None

@router.message(CommandStart(deep_link=True, magic=F.args.startswith(PAIR_PREFIX)))
async def start_pairing(message: Message, command: CommandObject):
    who = message.from_user
    lang = await _lang_of(who)
    code = invites.tidy((command.args or "")[len(PAIR_PREFIX):])
    found = pairing.describe(code)
    linking = _links_telegram(found)
    if not who or (not linking and not await _may_pair(getattr(message, "bot", None), who.id)):
        await message.answer(t("dsr.pair.not_open", lang), parse_mode="HTML")
        return
    if message.chat.type != "private":
        await message.answer(t("dsr.pair.in_private", lang))
        return

    if found is None:
        await message.answer(t("dsr.pair.gone", lang))
        return
    if linking:
        await message.answer(
            t("dsr.link.card", lang, name=escape_html(found.machine.name), code=invites.pretty(code)),
            parse_mode="HTML",
            reply_markup=_pair_keyboard(code, lang, "dsr.link.yes"),
        )
        return

    await message.answer(
        t("dsr.pair.card", lang,
          name=escape_html(found.machine.name), about=_about(found.machine, lang),
          code=invites.pretty(code)),
        parse_mode="HTML",
        reply_markup=_pair_keyboard(code, lang),
    )

@router.callback_query(F.data.startswith("pair:"))
async def answer_pairing(callback: CallbackQuery):
    who = callback.from_user
    lang = await _lang_of(who)
    _, verdict, code = callback.data.split(":", 2)
    linking = _links_telegram(pairing.describe(code))
    if not who or (not linking and not await _may_pair(getattr(callback, "bot", None), who.id)):
        await callback.answer(t("dsr.pair.not_open_short", lang), show_alert=True)
        return

    if linking:
        asked = pairing.answered(code)
        if verdict != "yes" or asked is None:
            said = t("dsr.pair.declined", lang)
        else:
            from db.database import get_db_session
            from services.render_farm import players

            async with get_db_session() as session:
                outcome = await players.link_telegram(session, asked.wants_telegram_for, who.id)
            said = (t("dsr.link.done", lang, name=escape_html(asked.machine.name))
                    if outcome == players.LINKED else t("dsr.link.taken", lang))
    elif verdict == "yes":
        linked = pairing.approve(code, who.id, who.full_name or "")
        said = (t("dsr.pair.linked", lang, name=escape_html(linked.machine.name))
                if linked else t("dsr.pair.gone", lang))
    else:
        pairing.decline(code)
        said = t("dsr.pair.declined", lang)

    if callback.message:
        await callback.message.edit_text(said, parse_mode="HTML")
    await callback.answer()

@router.message(Command("start"))
async def send_welcome_command(message: Message):
    await _send_welcome(message)

@router.message(TextTriggerFilter("start"))
async def send_welcome_trigger(message: Message, trigger_args: TriggerArgs):
    await _send_welcome(message)
