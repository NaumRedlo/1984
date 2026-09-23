from aiogram import Router, types, F
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from bot.filters import TextTriggerFilter, TriggerArgs
from utils.i18n import t
from utils.language import get_language
from utils.logger import get_logger

logger = get_logger("handlers.help")
router = Router(name="help")

_SECTION_CODES = ("osu", "account")

def _sections(user_id: int | None) -> tuple[str, ...]:
    return _SECTION_CODES

def _home_kb(lang: str, codes: tuple[str, ...]) -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(codes), 2):
        rows.append([
            InlineKeyboardButton(text=t(f"help.sec.{c}.label", lang), callback_data=f"help_{c}")
            for c in codes[i:i + 2]
        ])
    rows.append([InlineKeyboardButton(text=t("help.btn.close", lang), callback_data="help_close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _back_kb(lang: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=t("help.btn.back", lang), callback_data="help_main"),
        InlineKeyboardButton(text=t("help.btn.close", lang), callback_data="help_close"),
    ]])

@router.message(TextTriggerFilter("help"))
async def help_command(message: types.Message, trigger_args: TriggerArgs = None):
    who = message.from_user
    lang = (await get_language(who.id)).lower() if who else "en"
    await message.answer(
        t("help.home", lang),
        reply_markup=_home_kb(lang, _sections(who.id if who else None)),
        parse_mode="HTML",
    )

@router.callback_query(F.data.startswith("help_"))
async def process_help_callback(callback: CallbackQuery):
    who = callback.from_user
    lang = (await get_language(who.id)).lower() if who else "en"
    codes = _sections(who.id if who else None)
    action = callback.data.replace("help_", "", 1)
    if action == "close":
        try:
            await callback.message.delete()
        except Exception:
            pass
        await callback.answer()
        return

    if action == "main":
        text, kb = t("help.home", lang), _home_kb(lang, codes)
    elif action in codes:
        text, kb = t(f"help.sec.{action}.body", lang), _back_kb(lang)
    else:
        await callback.answer()
        return

    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        pass
    await callback.answer()

__all__ = ["router"]
