from aiogram import Router
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.filters import Command

from bot.filters import TextTriggerFilter, TriggerArgs
from utils.formatting.text import escape_html
from utils.i18n import t
from utils.language import get_language

router = Router(name="start")


async def _send_welcome(message: Message):
    lang = (await get_language(message.from_user.id)).lower() if message.from_user else "en"
    name = escape_html(message.from_user.first_name or "")
    await message.answer(
        t("start.welcome", lang, sep="═" * 30, name=name),
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Command("start"))
async def send_welcome_command(message: Message):
    await _send_welcome(message)


@router.message(TextTriggerFilter("start"))
async def send_welcome_trigger(message: Message, trigger_args: TriggerArgs):
    await _send_welcome(message)
