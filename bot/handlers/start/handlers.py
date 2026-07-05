from aiogram import Router
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.filters import Command

from bot.filters import TextTriggerFilter, TriggerArgs

router = Router(name="start")


async def _send_welcome(message: Message):
    name = message.from_user.first_name
    await message.answer(
        f"<b>PROJECT 1984: CLASSIFIED</b>\n"
        f"{'═' * 30}\n\n"
        f"Добро пожаловать, <b>{name}</b>.\n"
        f"Вам предоставлен доступ к системе наблюдения.\n\n"
        f"<b>Быстрый старт:</b>\n"
        f"• <code>register [никнейм]</code> — Привязать osu! аккаунт\n"
        f"• <code>pf</code> — Статистика и ранг\n"
        f"• <code>rs</code> — Последняя сыгранная карта\n"
        f"• <code>tpp</code> — Топ-плеи\n"
        f"• <code>tt</code> — Коллекция титулов\n"
        f"• <code>cmp [игрок]</code> — Сравнение статистики\n"
        f"• <code>lb</code> — Таблица лидеров\n"
        f"• <code>help</code> — Полный список директив\n\n"
        f"<i>Большой Брат следит за вашим рангом.</i>",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


@router.message(Command("start"))
async def send_welcome_command(message: Message):
    await _send_welcome(message)


@router.message(TextTriggerFilter("start"))
async def send_welcome_trigger(message: Message, trigger_args: TriggerArgs):
    await _send_welcome(message)
