"""Auto map-card: when an osu! beatmap link is pasted in chat, reply with a
rendered card for the map plus a 🔊 button that sends the 10-second preview.

Reacts to any message whose text contains a beatmap link (DM or group — in
groups the bot needs privacy mode OFF to see plain messages). Command messages
are handled by their own routers first, so a link inside e.g. /import never
reaches here.
"""

from __future__ import annotations

from collections import OrderedDict

from aiogram import Router, types, F
from aiogram.types import (
    BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
)

from services.image import card_renderer
from utils.formatting.text import escape_html
from utils.logger import get_logger
from utils.osu.beatmap_link import extract_beatmap_ref, LINK_HINT_RE
from utils.osu.preview import fetch_preview_mp3

logger = get_logger(__name__)

router = Router(name="maplink")

# Only wake for messages that actually carry a beatmap link (search, not match —
# links are usually mid-text). Channels are skipped; DMs and groups handled.
_LINK_FILTER = F.text.func(lambda t: bool(t) and bool(LINK_HINT_RE.search(t)))

# set_id -> {"title", "artist"} so the 🔊 callback can tag the audio without a
# second API round-trip. Bounded; lazily trimmed.
_preview_meta: "OrderedDict[int, dict]" = OrderedDict()
_PREVIEW_META_MAX = 256


def _remember_meta(set_id: int, title: str, artist: str) -> None:
    if not set_id:
        return
    _preview_meta[set_id] = {"title": title, "artist": artist}
    _preview_meta.move_to_end(set_id)
    while len(_preview_meta) > _PREVIEW_META_MAX:
        _preview_meta.popitem(last=False)


def _pick_diff(beatmaps: list[dict]) -> dict | None:
    """For a set-only link, show the hardest osu!std difficulty."""
    if not beatmaps:
        return None
    osu = [b for b in beatmaps if (b.get("mode_int") == 0 or b.get("mode") == "osu")]
    pool = osu or beatmaps
    return max(pool, key=lambda b: float(b.get("difficulty_rating") or 0.0))


def _covers_url(bset: dict, set_id) -> str | None:
    covers = (bset or {}).get("covers") or {}
    return (covers.get("cover@2x") or covers.get("cover")
            or (f"https://assets.ppy.sh/beatmaps/{set_id}/covers/cover@2x.jpg"
                if set_id else None))


def _card_from_beatmap(bm: dict) -> dict:
    bset = bm.get("beatmapset") or {}
    set_id = bm.get("beatmapset_id") or bset.get("id")
    bid = bm.get("id")
    return {
        "beatmap_id": bid,
        "beatmapset_id": set_id,
        "title": bset.get("title") or bm.get("title") or "???",
        "artist": bset.get("artist") or "",
        "creator": bset.get("creator") or "",
        "version": bm.get("version") or "",
        "star_rating": bm.get("difficulty_rating"),
        "cs": bm.get("cs"), "ar": bm.get("ar"),
        "od": bm.get("accuracy"), "hp_drain": bm.get("drain"),
        "bpm": bm.get("bpm"), "length": bm.get("total_length"),
        "max_combo": bm.get("max_combo"),
        "status": bm.get("status") or bset.get("status"),
        "cover_url": _covers_url(bset, set_id),
        "url": bm.get("url") or (
            f"https://osu.ppy.sh/beatmapsets/{set_id}#osu/{bid}" if set_id
            else f"https://osu.ppy.sh/beatmaps/{bid}"),
    }


def _card_from_set(bs: dict, diff: dict) -> dict:
    set_id = bs.get("id")
    bid = diff.get("id")
    return {
        "beatmap_id": bid,
        "beatmapset_id": set_id,
        "title": bs.get("title") or "???",
        "artist": bs.get("artist") or "",
        "creator": bs.get("creator") or "",
        "version": diff.get("version") or "",
        "star_rating": diff.get("difficulty_rating"),
        "cs": diff.get("cs"), "ar": diff.get("ar"),
        "od": diff.get("accuracy"), "hp_drain": diff.get("drain"),
        "bpm": diff.get("bpm") or bs.get("bpm"),
        "length": diff.get("total_length"),
        "max_combo": diff.get("max_combo"),
        "status": diff.get("status") or bs.get("status"),
        "cover_url": _covers_url(bs, set_id),
        "url": f"https://osu.ppy.sh/beatmapsets/{set_id}#osu/{bid}",
    }


async def _resolve_card(ref, api) -> dict | None:
    """Turn a BeatmapRef into card data via the osu! API, or None."""
    if ref.beatmap_id:
        bm = await api.get_beatmap(ref.beatmap_id)
        if bm:
            return _card_from_beatmap(bm)
    if ref.beatmapset_id:
        bs = await api.get_beatmapset(ref.beatmapset_id)
        if bs:
            diff = _pick_diff(bs.get("beatmaps") or [])
            if diff:
                return _card_from_set(bs, diff)
    return None


@router.message(_LINK_FILTER, F.chat.type.in_({"private", "group", "supergroup"}))
async def on_beatmap_link(message: types.Message, osu_api_client):
    text = message.text or ""
    if text.lstrip().startswith("/"):
        return  # a command carrying a link — let its own handler own it
    ref = extract_beatmap_ref(text)
    if not ref:
        return

    try:
        data = await _resolve_card(ref, osu_api_client)
    except Exception:
        logger.warning("maplink: resolve failed", exc_info=True)
        return
    if not data:
        return  # unknown/deleted map — stay silent rather than nag

    set_id = data.get("beatmapset_id") or 0
    _remember_meta(set_id, data.get("title") or "", data.get("artist") or "")

    try:
        png = (await card_renderer.generate_map_card_async(data)).getvalue()
    except Exception:
        logger.warning("maplink: render failed", exc_info=True)
        return

    sr = float(data.get("star_rating") or 0.0)
    caption = (
        f"<b>{escape_html(data.get('artist') or '')} — "
        f"{escape_html(data.get('title') or '???')}</b>\n"
        f"[{escape_html(data.get('version') or '')}] · ★{sr:.2f}"
    )
    buttons = [InlineKeyboardButton(text="🔗 osu!", url=data["url"])]
    if set_id:
        buttons.append(InlineKeyboardButton(
            text="🔊 Превью", callback_data=f"mapprev:{set_id}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[buttons])

    try:
        await message.answer_photo(
            BufferedInputFile(png, filename="map.png"),
            caption=caption, parse_mode="HTML", reply_markup=kb,
        )
    except Exception:
        logger.warning("maplink: send_photo failed", exc_info=True)


@router.callback_query(F.data.startswith("mapprev:"))
async def on_map_preview(callback: types.CallbackQuery):
    try:
        set_id = int(callback.data.split(":")[1])
    except (IndexError, ValueError):
        await callback.answer("Некорректные данные.", show_alert=True)
        return

    await callback.answer("Готовлю превью…")
    mp3 = await fetch_preview_mp3(set_id)
    if not mp3:
        await callback.answer("У этой карты нет превью 🤷", show_alert=True)
        return

    meta = _preview_meta.get(set_id) or {}
    title = meta.get("title") or "osu! preview"
    artist = meta.get("artist") or "osu!"
    try:
        await callback.message.answer_audio(
            BufferedInputFile(mp3, filename=f"{set_id}.mp3"),
            title=title, performer=artist,
            reply_to_message_id=callback.message.message_id,
        )
    except Exception:
        logger.warning("maplink: send_audio failed", exc_info=True)
        await callback.answer("Не удалось отправить превью.", show_alert=True)
