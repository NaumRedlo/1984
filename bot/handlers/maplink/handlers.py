from __future__ import annotations

from aiogram import Router, types, F
from aiogram.types import BufferedInputFile

from services.image import card_renderer
from utils.language import get_language
from utils.logger import get_logger
from utils.osu.beatmap_link import extract_beatmap_ref, LINK_HINT_RE
from utils.osu.helpers import remember_message_context

from bot.handlers.maplink.whatif import _build_whatif_data, _whatif_keyboard

logger = get_logger(__name__)

router = Router(name="maplink")

_LINK_FILTER = F.text.func(lambda t: bool(t) and bool(LINK_HINT_RE.search(t)))

_DEFAULT_ACCURACY = 100.0

@router.message(_LINK_FILTER, F.chat.type.in_({"private", "group", "supergroup"}))
async def on_beatmap_link(message: types.Message, osu_api_client):
    text = message.text or ""
    if text.lstrip().startswith("/"):
        return
    ref = extract_beatmap_ref(text)
    if not ref:
        return

    lang = (await get_language(message.from_user.id)).lower() if message.from_user else "en"
    data = await _build_whatif_data(ref, _DEFAULT_ACCURACY, "", osu_api_client, lang)
    if not data:
        return

    try:
        png = (await card_renderer.generate_whatif_card_async(data)).getvalue()
    except Exception:
        logger.warning("maplink: render failed", exc_info=True)
        return

    kb = _whatif_keyboard(data["beatmap_id"], data["accuracy"], data["mods"], data["url"], lang=lang)
    try:
        sent = await message.answer_photo(
            BufferedInputFile(png, filename="map.png"), reply_markup=kb,
        )

        remember_message_context(sent.chat.id, sent.message_id, {
            "beatmap_id": data.get("beatmap_id"), "beatmapset_id": data.get("beatmapset_id"),
        })
    except Exception:
        logger.warning("maplink: send_photo failed", exc_info=True)
