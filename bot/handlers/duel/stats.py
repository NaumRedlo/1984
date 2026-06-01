from aiogram import Router
from aiogram.types import BufferedInputFile, Message

from bot.filters import TextTriggerFilter
from bot.handlers.duel.common import build_duel_keyboard, get_duel_data
from services.image import card_renderer

router = Router(name="duel.stats")


@router.message(TextTriggerFilter("duelstats", "duels"))
async def cmd_duel_stats(message: Message):
    tg_id = message.from_user.id
    mode = "casual"
    data = await get_duel_data(tg_id, mode)
    if not data:
        await message.answer("Вы не зарегистрированы.")
        return
    img_buf = await card_renderer.generate_duel_card_async(data)
    await message.answer_photo(
        BufferedInputFile(img_buf.read(), filename="duel.png"),
        reply_markup=build_duel_keyboard(tg_id, mode),
    )
