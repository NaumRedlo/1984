from aiogram import Router
from aiogram.types import BufferedInputFile, Message

from bot.filters import TextTriggerFilter
from bot.handlers.bsk.common import build_bsk_keyboard, get_bsk_data
from services.image import card_renderer

router = Router(name="bsk.stats")


@router.message(TextTriggerFilter("bskstats", "bsks"))
async def cmd_bsk_stats(message: Message):
    tg_id = message.from_user.id
    mode = "casual"
    data = await get_bsk_data(tg_id, mode)
    if not data:
        await message.answer("Вы не зарегистрированы.")
        return
    img_buf = await card_renderer.generate_bsk_card_async(data)
    await message.answer_photo(
        BufferedInputFile(img_buf.read(), filename="bsk.png"),
        reply_markup=build_bsk_keyboard(tg_id, mode),
    )
