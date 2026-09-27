from aiogram import F, Router, types

from services.render_farm import orders, skin_upload
from utils.language import get_language

router = Router(name="farm")

def _a_replay(name) -> bool:
    return bool(name) and str(name).lower().endswith(".osr")

@router.message(F.document.file_name.func(_a_replay))
async def on_replay(message: types.Message, osu_api_client=None, **_) -> None:
    lang = (await get_language(message.from_user.id)).lower() if message.from_user else "en"
    await orders.take(message.bot, message, lang, osu_api_client)

def _a_skin(name) -> bool:
    return bool(name) and str(name).lower().endswith((".osk", ".zip"))

@router.message(F.document.file_name.func(_a_skin))
async def on_skin(message: types.Message, **_) -> None:
    lang = (await get_language(message.from_user.id)).lower() if message.from_user else "en"
    await skin_upload.take(message.bot, message, lang)

__all__ = ["router"]
