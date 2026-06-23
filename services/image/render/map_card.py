"""Single-map info card — the reply the bot posts when an osu! beatmap link
shows up in chat.

A landscape "beatmapset card": the cover fills the panel under a vertical dark
gradient (so text stays readable), with the title/artist/mapper overlaid near
the bottom, an SR badge top-right tinted to the osu! difficulty colour, a status
pill top-left, and a stat strip (length · BPM · combo · CS/AR/OD/HP) along the
foot. Shares the SR spectrum + colour helpers with the duel pool card.
"""

from io import BytesIO
from typing import Dict, Optional

from PIL import Image, ImageDraw

from services.image.constants import TEXT_PRIMARY, TEXT_SECONDARY
from services.image.utils import download_image, cover_center_crop, load_icon
from services.image.render.duel_pool_card import (
    _sr_color, _white_icon,
)
from utils.formatting.text import format_length


# Canvas geometry — wide enough for a long title at a comfortable font size.
_W = 760
_H = 300
_RADIUS = 20
_PAD = 24

_PANEL = (24, 26, 36)
_STRIP = (10, 11, 18)                 # foot stat-strip backdrop
_WHITE = (255, 255, 255)

# Ranked-status pill colours (osu!-ish): ranked/approved green, loved pink,
# qualified blue, everything else (pending/graveyard/wip) muted grey.
_STATUS_COLORS = {
    "ranked": (118, 188, 86), "approved": (118, 188, 86),
    "loved": (255, 102, 171), "qualified": (102, 170, 255),
}
_STATUS_DEFAULT = (120, 122, 140)


def _status_pill_color(status: Optional[str]) -> tuple:
    return _STATUS_COLORS.get((status or "").lower(), _STATUS_DEFAULT)


def _vertical_shade(w: int, h: int, top_a: int, bot_a: int) -> Image.Image:
    """Black RGBA layer whose alpha ramps top_a→bot_a down the height."""
    col = Image.new("L", (1, h))
    for y in range(h):
        col.putpixel((0, y), int(top_a + (bot_a - top_a) * (y / max(1, h - 1))))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    layer.putalpha(col.resize((w, h)))
    return layer


class MapCardMixin:

    def generate_map_card(
        self, data: Dict, cover: Optional[Image.Image] = None,
    ) -> BytesIO:
        w, h = _W, _H
        card = Image.new("RGBA", (w, h), (0, 0, 0, 0))

        # ── Body + cover background under a darkening gradient ───────────────
        self._aa_rounded_fill(card, (0, 0, w, h), radius=_RADIUS, fill=_PANEL)
        mask = self._rounded_mask((w, h), _RADIUS)
        if cover is not None:
            try:
                bg = cover_center_crop(cover.convert("RGBA"), w, h)
                bg = Image.alpha_composite(bg, _vertical_shade(w, h, 60, 225))
                card.paste(bg, (0, 0), mask)
            except Exception:
                pass
        draw = ImageDraw.Draw(card)

        sr = float(data.get("star_rating") or 0.0)

        # ── Status pill (top-left) ───────────────────────────────────────────
        status = (data.get("status") or "").upper()
        if status:
            col = _status_pill_color(data.get("status"))
            tw, th = self._text_size(draw, status, self.font_stat_label)
            px, py = 10, 5
            self._aa_rounded_fill(card, (_PAD, 18, _PAD + tw + px * 2, 18 + th + py * 2),
                                  radius=(th + py * 2) // 2, fill=col)
            d2 = ImageDraw.Draw(card)
            d2.text((_PAD + px, 18 + py - 1), status, font=self.font_stat_label,
                    fill=(15, 15, 18))
            draw = ImageDraw.Draw(card)

        # ── SR badge (top-right), tinted to the osu! difficulty colour ───────
        self._draw_sr_badge_centered(card, draw, w - _PAD - 36, 30, sr)
        draw = ImageDraw.Draw(card)

        # ── Title / artist / mapper block, lifted off the foot strip ─────────
        text_w = w - _PAD * 2
        artist = self._fit_pool(draw, str(data.get("artist") or ""),
                                self.font_subtitle, text_w)
        title = self._fit_pool(draw, str(data.get("title") or "???"),
                               self.font_big, text_w)
        creator = str(data.get("creator") or "")
        version = str(data.get("version") or "")
        meta = f"[{version}]" + (f"  ·  mapped by {creator}" if creator else "")
        meta = self._fit_pool(draw, meta, self.font_label, text_w)

        self._draw_text(draw, (_PAD, h - 150), artist, self.font_subtitle,
                        TEXT_SECONDARY, shadow=True)
        self._draw_text(draw, (_PAD, h - 126), title, self.font_big,
                        _WHITE, shadow=True)
        self._draw_text(draw, (_PAD, h - 86), meta, self.font_label,
                        (170, 178, 215), shadow=True)

        # ── Foot stat strip ──────────────────────────────────────────────────
        strip_h = 46
        sy = h - strip_h
        strip = _vertical_shade(w, strip_h, 150, 150)
        card.paste(strip, (0, sy), strip)
        draw = ImageDraw.Draw(card)
        self._draw_stat_strip(card, draw, _PAD, sy + (strip_h - 16) // 2, data)

        # ── Compose onto an opaque rounded canvas ────────────────────────────
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        out.paste(card, (0, 0), mask)
        return self._save(out.convert("RGB"))

    def _draw_stat_strip(self, img, draw, x: int, y: int, data: Dict) -> None:
        """One horizontal row: ⏱length · BPM · ✕combo · CS/AR/OD/HP."""
        cx = x

        def icon_text(name: str, text: str, gap: int = 14):
            nonlocal cx
            ic = _white_icon(load_icon(name, size=14)) if name else None
            if ic is not None:
                ic = self._tint_icon(ic, _WHITE)
                img.paste(ic, (cx, y + 1), ic)
                cx += ic.width + 4
            d = ImageDraw.Draw(img)
            self._draw_text(d, (cx, y), text, self.font_small, _WHITE)
            tw, _ = self._text_size(d, text, self.font_small)
            cx += tw + gap

        length = format_length(data.get("length"))
        if length and length != "—":
            icon_text("timer", length)
        bpm = int(round(float(data.get("bpm") or 0)))
        if bpm:
            icon_text("bpm", str(bpm))
        combo = int(data.get("max_combo") or 0)
        if combo:
            icon_text("", f"{combo}x")

        # CS/AR/OD/HP as compact "LABEL value" chips, right side of the strip.
        def stat(label: str, key: str):
            nonlocal cx
            v = float(data.get(key) or 0.0)
            chip = f"{label} {v:g}"
            d = ImageDraw.Draw(img)
            self._draw_text(d, (cx, y), chip, self.font_small, (200, 206, 230))
            tw, _ = self._text_size(d, chip, self.font_small)
            cx += tw + 12

        for label, key in (("CS", "cs"), ("AR", "ar"), ("OD", "od"), ("HP", "hp_drain")):
            stat(label, key)

    async def generate_map_card_async(self, data: Dict) -> BytesIO:
        cover = None
        url = data.get("cover_url")
        if not url and data.get("beatmapset_id"):
            url = (f"https://assets.ppy.sh/beatmaps/"
                   f"{data['beatmapset_id']}/covers/cover@2x.jpg")
        if url:
            r = await download_image(url)
            cover = r if (r and not isinstance(r, Exception)) else None
        return self.generate_map_card(data, cover)
