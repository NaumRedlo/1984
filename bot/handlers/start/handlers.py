from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

from bot.filters import TextTriggerFilter, TriggerArgs
from bot.keyboards.reply_keyboard import get_main_keyboard

router = Router(name="start")


async def _send_welcome(message: Message):
    name = message.from_user.first_name
    await message.answer(
        f"<b>PROJECT 1984: CLASSIFIED</b>\n"
        f"{'═' * 30}\n\n"
        f"Добро пожаловать, <b>{name}</b>.\n"
        f"Вам предоставлен доступ к системе наблюдения <b>Отдела Баунти</b>.\n\n"
        f"<b>Быстрый старт:</b>\n"
        f"• <code>register [никнейм]</code> — Привязать osu! аккаунт\n"
        f"• <code>profile</code> / <code>pf</code> — Статистика и ранг охотника\n"
        f"• <code>duelhistory</code> / <code>dh</code> — История дуэлей\n"
        f"• <code>rs</code> — Последняя сыгранная карта\n"
        f"• <code>hps</code> — Анализ потенциала HP карты\n"
        f"• <code>compare [игрок]</code> — Сравнение статистики\n"
        f"• <code>leaderboard</code> — Таблица лидеров\n"
        f"• <code>bountylist</code> — Активные баунти\n"
        f"• <code>help</code> — Полный список директив\n\n"
        f"<i>Большой Брат следит за вашим рангом.</i>",
        parse_mode="HTML",
        reply_markup=get_main_keyboard(),
    )


@router.message(Command("start"))
async def send_welcome_command(message: Message):
    await _send_welcome(message)


@router.message(TextTriggerFilter("start"))
async def send_welcome_trigger(message: Message, trigger_args: TriggerArgs):
    await _send_welcome(message)
