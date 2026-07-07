"""Auto map-card: when an osu! beatmap link is pasted in chat, the bot
"notices" it and posts the interactive what-if card directly (default:
100% nomod) — the same card/keyboard `map <accuracy> [mods]` produces, just
started for you. Adjust via the buttons, or reply with `map 94 hr` to
jump straight to a specific accuracy.

Reacts to any message whose text contains a beatmap link (DM or group — in
groups the bot needs privacy mode OFF to see plain messages). Command messages
are handled by their own routers first, so a link inside e.g. /import never
reaches here.
"""

from __future__ import annotations

from aiogram import Router, types, F
from aiogram.types import BufferedInputFile

from services.image import card_renderer
from utils.logger import get_logger
from utils.osu.beatmap_link import extract_beatmap_ref, LINK_HINT_RE
from utils.osu.helpers import remember_message_context

from bot.handlers.maplink.whatif import _build_whatif_data, _whatif_keyboard

logger = get_logger(__name__)

router = Router(name="maplink")

# Only wake for messages that actually carry a beatmap link (search, not match —
# links are usually mid-text). Channels are skipped; DMs and groups handled.
_LINK_FILTER = F.text.func(lambda t: bool(t) and bool(LINK_HINT_RE.search(t)))

# Starting point for the auto-posted what-if card — a clean "what would an FC
# be worth" baseline; the interactive keyboard adjusts it from here.
_DEFAULT_ACCURACY = 100.0


@router.message(_LINK_FILTER, F.chat.type.in_({"private", "group", "supergroup"}))
async def on_beatmap_link(message: types.Message, osu_api_client):
    text = message.text or ""
    if text.lstrip().startswith("/"):
        return  # a command carrying a link — let its own handler own it
    ref = extract_beatmap_ref(text)
    if not ref:
        return

    data = await _build_whatif_data(ref, _DEFAULT_ACCURACY, "", osu_api_client)
    if not data:
        return  # unknown/deleted map, or pp calc failed — stay silent rather than nag

    try:
        png = (await card_renderer.generate_whatif_card_async(data)).getvalue()
    except Exception:
        logger.warning("maplink: render failed", exc_info=True)
        return

    kb = _whatif_keyboard(data["beatmap_id"], data["accuracy"], data["mods"], data["url"])
    try:
        sent = await message.answer_photo(
            BufferedInputFile(png, filename="map.png"), reply_markup=kb,
        )
        # Lets "map <accuracy> [mods]" resolve the beatmap when replying to
        # THIS card rather than needing the raw link in the reply itself.
        remember_message_context(sent.chat.id, sent.message_id, {
            "beatmap_id": data.get("beatmap_id"), "beatmapset_id": data.get("beatmapset_id"),
        })
    except Exception:
        logger.warning("maplink: send_photo failed", exc_info=True)
