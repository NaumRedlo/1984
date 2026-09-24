from aiogram import F, Router, types

from services.render_farm import orders
from utils.language import get_language

router = Router(name="farm")

def _a_replay(name) -> bool:
    return bool(name) and str(name).lower().endswith(".osr")

@router.message(F.document.file_name.func(_a_replay))
async def on_replay(message: types.Message, osu_api_client=None, **_) -> None:
    lang = (await get_language(message.from_user.id)).lower() if message.from_user else "en"
    await orders.take(message.bot, message, lang, osu_api_client)

__all__ = ["router"]
