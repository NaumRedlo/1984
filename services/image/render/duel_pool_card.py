"""Duel map-pool card — six maps as a flat row of playing cards.

Sent to each player (in DM) when a duel is accepted: that player's OWN
auto-built pool of 6 maps. Each player gets a distinct set (both built around
the shared average SR); rounds alternate the two pools, weaker player first.

Layout: a single left-to-right row of 6 portrait poker cards (2.5:3.5 ratio)
laid flat across the full width — no rotation, reads like a row of cards on a
table. Each card shows its index as a top-left "rank" pip, the beatmap cover
under a dark overlay, an SR badge, title / artist, difficulty name, length +
BPM, and the CS/AR/OD/HP read-outs.
"""

import asyncio
from io import BytesIO
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFont

from services.image.constants import (
    TEXT_PRIMARY, TEXT_SECONDARY, ACCENT_RED, PADDING_X, TORUS_BOLD,
)
from services.image.utils import download_image, cover_center_crop, load_icon, _find_font


# SR → colour: the official osu! difficulty spectrum (osu!lazer
# OsuColour.ForStarDifficulty) — grey → blue → cyan → teal → green → yellow →
# orange → red → magenta → indigo → near-black, sampled with a linear gradient
# between the anchor stars below. The duplicate 0.1 anchor is osu!'s hard step
# from the "no difficulty" grey into the blue ramp.
_SR_STOPS = [
    (0.1, (170, 170, 170)),    # #aaaaaa
    (0.1, (66, 144, 251)),     # #4290fb
    (1.25, (79, 192, 255)),    # #4fc0ff
    (2.0, (79, 255, 213)),     # #4fffd5
    (2.5, (124, 255, 79)),     # #7cff4f
    (3.3, (246, 240, 92)),     # #f6f05c
    (4.2, (255, 128, 104)),    # #ff8068
    (4.9, (255, 78, 111)),     # #ff4e6f
    (5.8, (198, 69, 184)),     # #c645b8
    (6.7, (101, 99, 222)),     # #6563de
    (7.7, (24, 21, 142)),      # #18158e
    (9.0, (0, 0, 0)),          # black
]


def _sr_color(sr: float) -> tuple:
    sr = float(sr or 0.0)
    if sr <= _SR_STOPS[0][0]:
        return _SR_STOPS[0][1]
    if sr >= _SR_STOPS[-1][0]:
        return _SR_STOPS[-1][1]
    for (lo, c_lo), (hi, c_hi) in zip(_SR_STOPS, _SR_STOPS[1:]):
        if lo <= sr <= hi:
            t = (sr - lo) / (hi - lo) if hi > lo else 0.0
            return tuple(int(round(a + (b - a) * t)) for a, b in zip(c_lo, c_hi))
    return _SR_STOPS[-1][1]


def _fmt_len(seconds: Optional[int]) -> str:
    s = int(seconds or 0)
    return f"{s // 60}:{s % 60:02d}" if s else "—"


def _white_icon(icon: Optional[Image.Image]) -> Optional[Image.Image]:
    """Recolour an icon to solid white, keeping its alpha silhouette."""
    if icon is None:
        return None
    icon = icon.convert("RGBA")
    solid = Image.new("RGBA", icon.size, (255, 255, 255, 255))
    solid.putalpha(icon.getchannel("A"))
    return solid


# ── Card geometry ────────────────────────────────────────────────────────────
# Poker proportion 2.5:3.5 → 200×280 keeps text legible at this scale.
_CARD_W = 200
_CARD_H = 280
_CARD_RADIUS = 16
_COVER_H = 116                  # top band that holds the beatmap cover
_INNER_PAD = 12

# Row geometry — six cards laid flat horizontally across the canvas, evenly
# spaced edge-to-edge with a small gap. No rotation; reads as a row of cards
# on a table rather than a hand held in front of you.
_ROW_GAP = 10                    # gap between adjacent cards (px)
_ROW_SIDE_PAD = 18               # outer padding on the left/right of the row
_ROW_TOP_DROP = 70               # push the whole row this many px below the sub-header

_PANEL = (28, 30, 42)
_PANEL_BORDER = (78, 78, 102)
_BAR_TRACK = (44, 46, 60)
_BAR_FILL = (224, 78, 92)
_WHITE = (255, 255, 255)         # stat values, meta values, and their icons
# Top-tier rank pip: at/above _PIP_GOLD_SR the osu! spectrum fades to near-black
# and vanishes on the dark card, so the numeral switches to a dark-purple fill
# with a thin gold outline — legible, and a clear "this is a monster" flag.
_PIP_GOLD_SR = 7.0
_PIP_PURPLE = (108, 52, 168)
_PIP_GOLD = (240, 196, 90)

# Played-card overlay: a dark wash dims the whole (upright) card and a red
# "PLAYED" stamp is laid diagonally (45°) across it — legible on any cover, and
# nothing leaves the card's own slot.
_PLAYED_DIM = (6, 6, 10, 168)            # RGBA wash composited over the card body
_PLAYED_STAMP_RED = (224, 60, 72)
_PLAYED_STAMP_OUTLINE = (12, 6, 10)


class DuelPoolCardMixin:

    def generate_duel_pool_card(
        self,
        data: Dict,
        covers: Optional[List[Optional[Image.Image]]] = None,
    ) -> BytesIO:
        maps = (data.get("maps", []) or [])[:6]
        covers = covers or [None] * len(maps)

        mode = str(data.get("mode", "casual"))
        mode_label = "RANKED" if mode == "ranked" else "CASUAL"
        total_rounds = int(data.get("total_rounds", 0) or 0)
        win_target = int(data.get("win_target", 0) or 0)
        target_sr = float(data.get("target_sr", 0.0) or 0.0)

        n = len(maps)

        # Canvas — one flat row of cards across the full width. Width is sized to
        # hold every card edge-to-edge with `_ROW_GAP` between them and
        # `_ROW_SIDE_PAD` margins; height fits the header, sub-row, and a single
        # upright card. `_ROW_TOP_DROP` pushes the row down so the header +
        # sub-row breathe above the cards.
        header_h = 46
        sub_h = 30
        row_top = header_h + sub_h + 18 + _ROW_TOP_DROP
        cards = max(n, 1)
        W = _ROW_SIDE_PAD * 2 + cards * _CARD_W + (cards - 1) * _ROW_GAP
        H = row_top + _CARD_H + 24

        img, draw = self._create_canvas(W, H)

        # ── Header ───────────────────────────────────────────────────────────
        draw.rectangle([(0, 0), (W, header_h)], fill=(18, 18, 28))
        self._text_center(draw, W // 2, 8, "DUEL · MAP POOL", self.font_big,
                          ACCENT_RED, shadow=True)
        draw.line((0, header_h - 1, W, header_h - 1), fill=(40, 40, 55))

        sub_y = header_h + (sub_h - 18) // 2
        fmt = (f"{mode_label} · Bo{total_rounds} · TO {win_target}"
               if total_rounds else mode_label)
        self._draw_text(draw, (PADDING_X, sub_y), fmt, self.font_label,
                        TEXT_SECONDARY)
        if target_sr:
            tgt = f"~{target_sr:.1f}"
            star = load_icon("star", size=15)
            tw, _ = self._text_size(draw, tgt, self.font_label)
            sw = (star.width + 4) if star else 0
            sx = W - PADDING_X - tw - sw
            if star:
                img.paste(star, (sx, sub_y + 7), star)
                draw = ImageDraw.Draw(img)
                sx += star.width + 4
            self._draw_text(draw, (sx, sub_y + 5), tgt, self.font_label,
                            TEXT_PRIMARY)

        if n == 0:
            return self._save(img)

        # ── Flat row ─────────────────────────────────────────────────────────
        # Each card is built on its own canvas, then pasted upright in a single
        # left-to-right row. No rotation — reads as a row of cards on a table.
        for i, m in enumerate(maps):
            cov = covers[i] if i < len(covers) else None
            card = self._build_card(m, cov, i + 1)
            x = _ROW_SIDE_PAD + i * (_CARD_W + _ROW_GAP)
            img.paste(card, (x, row_top), card)

        return self._save(img)

    # ── Single playing card ──────────────────────────────────────────────────

    def _build_card(self, m: Dict, cover: Optional[Image.Image],
                    index: int) -> Image.Image:
        """Render one map as a portrait-oriented playing card on its own
        RGBA canvas so it can be rotated & pasted into the fan."""
        w, h = _CARD_W, _CARD_H
        card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(card)

        # Body
        self._aa_rounded_fill(card, (0, 0, w, h), radius=_CARD_RADIUS,
                              fill=_PANEL)
        draw = ImageDraw.Draw(card)

        # Cover band — rounded top, square bottom (sits inside the rounded body).
        mask = self._rounded_mask((w, _COVER_H), _CARD_RADIUS)
        md = ImageDraw.Draw(mask)
        md.rectangle((0, _CARD_RADIUS, w, _COVER_H), fill=255)
        if cover:
            try:
                thumb = cover_center_crop(cover.convert("RGBA"), w, _COVER_H)
                ov = Image.new("RGBA", (w, _COVER_H), (0, 0, 0, 90))
                thumb = Image.alpha_composite(thumb, ov)
                card.paste(thumb, (0, 0), mask)
            except Exception:
                pass
        draw = ImageDraw.Draw(card)

        # Cover/body separator
        draw.line([(0, _COVER_H), (w, _COVER_H)], fill=(60, 60, 80))

        # Border drawn AFTER the cover so the cover can't paint over it — the
        # full-width cover band used to bleed past the grey outline on the
        # top/left/right edges.  Box matches the body fill exactly (0,0,w,h):
        # an off-by-one (w-1, h-1) leaves a 1px sliver of fill/cover sticking
        # out past the frame on the right and bottom.
        self._aa_rounded_outline(card, (0, 0, w, h),
                                 radius=_CARD_RADIUS,
                                 outline=_PANEL_BORDER, width=2)
        draw = ImageDraw.Draw(card)

        # ── "Rank" pip (index) — top-left corner numeral, SR-tinted ──────────
        # Just the top-left numeral: the bottom-right mirror pip and the little
        # suit-star that used to sit under each pip were dropped to keep the
        # corner clean.
        sr = float(m.get("star_rating") or 0.0)
        if sr >= _PIP_GOLD_SR:
            # Dark-purple numeral, thinly gold-outlined — stands out where the
            # osu! spectrum would otherwise go near-black on the dark card.
            draw.text((10, 6), str(index), font=self.font_big,
                      fill=_PIP_PURPLE, stroke_width=1, stroke_fill=_PIP_GOLD)
        else:
            self._draw_text(draw, (10, 6), str(index), self.font_big,
                            _sr_color(sr), shadow=True)

        # ── SR badge — centred on the cover, like a card's central suit ─────
        self._draw_sr_badge_centered(card, draw, w // 2, _COVER_H - 19, sr)
        draw = ImageDraw.Draw(card)

        # ── Body text (under the cover) ─────────────────────────────────────
        body_x0 = _INNER_PAD
        body_x1 = w - _INNER_PAD
        body_w = body_x1 - body_x0

        title = self._fit_pool(draw, str(m.get("title") or "???"),
                               self.font_row, body_w)
        self._draw_text(draw, (body_x0, _COVER_H + 8), title,
                        self.font_row, TEXT_PRIMARY)

        # Artist only — mapper was dropped from this line (the difficulty name
        # below still carries the set's identity).
        artist = self._fit_pool(draw, str(m.get("artist") or ""),
                                self.font_small, body_w)
        if artist:
            self._draw_text(draw, (body_x0, _COVER_H + 30), artist,
                            self.font_small, TEXT_SECONDARY)

        # Difficulty name
        diff = self._fit_pool(draw, str(m.get("version") or "?"),
                              self.font_label, body_w)
        self._draw_text(draw, (body_x0, _COVER_H + 50), diff,
                        self.font_label, (150, 160, 200))

        # Length + BPM line — each value is prefixed with its icon (timer for
        # the duration, bpm for the tempo) so the row reads at a glance even
        # on a single-card scale. Drawn inline, separated by a thin dot.
        length_str = _fmt_len(m.get("length"))
        bpm = int(round(float(m.get("bpm") or 0)))
        bpm_str = f"{bpm}" if bpm else ""
        meta_y = _COVER_H + 72
        cx = body_x0
        timer_icon = _white_icon(load_icon("timer", size=12))
        bpm_icon = _white_icon(load_icon("bpm", size=12))
        if length_str and length_str != "—":
            if timer_icon:
                tinted_t = self._tint_icon(timer_icon, _WHITE)
                card.paste(tinted_t, (cx, meta_y + 2), tinted_t)
                cx += tinted_t.width + 3
                draw = ImageDraw.Draw(card)
            self._draw_text(draw, (cx, meta_y), length_str,
                            self.font_small, _WHITE)
            tw, _ = self._text_size(draw, length_str, self.font_small)
            cx += tw + 8
        if bpm_str:
            if bpm_icon:
                tinted_b = self._tint_icon(bpm_icon, _WHITE)
                card.paste(tinted_b, (cx, meta_y + 2), tinted_b)
                cx += tinted_b.width + 3
                draw = ImageDraw.Draw(card)
            self._draw_text(draw, (cx, meta_y), bpm_str,
                            self.font_small, _WHITE)

        # CS / AR / OD / HP bars — half-width, two columns, so they fit the
        # narrow card body without truncating.
        bar_y = _COVER_H + 96
        col_w = (body_w - 10) // 2
        for j, (label, key) in enumerate(
                (("CS", "cs"), ("AR", "ar"), ("OD", "od"), ("HP", "hp_drain"))):
            col = j % 2
            row = j // 2
            bx = body_x0 + col * (col_w + 10)
            by = bar_y + row * 20
            self._draw_mini_stat(card, draw, bx, by, col_w, label,
                                 m.get(key))
            draw = ImageDraw.Draw(card)

        # Already-played maps: dim the card and stamp a diagonal "PLAYED".
        if str(m.get("status") or "") == "played":
            self._apply_played_overlay(card)

        return card

    # ── played overlay ───────────────────────────────────────────────────────

    _stamp_font_cache: Optional[ImageFont.FreeTypeFont] = None

    def _played_stamp_font(self, size: int = 44) -> ImageFont.FreeTypeFont:
        if self._stamp_font_cache is None:
            path = _find_font(TORUS_BOLD)
            try:
                self._stamp_font_cache = (ImageFont.truetype(path, size) if path
                                          else getattr(self, "font_vs", self.font_big))
            except Exception:
                self._stamp_font_cache = getattr(self, "font_vs", self.font_big)
        return self._stamp_font_cache

    def _apply_played_overlay(self, card: Image.Image) -> None:
        """Dim ``card`` in place and lay a diagonal red 'PLAYED' stamp across it.

        The dim wash is composited over the card and then the card's *original*
        alpha is restored, so the rounded transparent corners stay transparent
        (no dark square bleeding past the card edge)."""
        w, h = card.size
        orig_alpha = card.getchannel("A")
        wash = Image.alpha_composite(card, Image.new("RGBA", (w, h), _PLAYED_DIM))
        wash.putalpha(orig_alpha)
        card.paste(wash, (0, 0))

        # Stamp drawn upright on its own layer, then rotated 45° and centred.
        text = "PLAYED"
        font = self._played_stamp_font()
        tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        l, t, r, b = tmp.textbbox((0, 0), text, font=font, stroke_width=3)
        tw, th = r - l, b - t
        pad = 10
        layer = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.text((pad - l, pad - t), text, font=font, fill=_PLAYED_STAMP_RED,
                stroke_width=3, stroke_fill=_PLAYED_STAMP_OUTLINE)
        rot = layer.rotate(45, expand=True, resample=Image.BICUBIC)
        card.paste(rot, ((w - rot.width) // 2, (h - rot.height) // 2), rot)

    # ── helpers ──────────────────────────────────────────────────────────────

    def _tint_icon(self, icon: Image.Image, color: tuple) -> Image.Image:
        """Recolour a white-silhouette icon to `color`, keeping its alpha."""
        rgba = icon.convert("RGBA")
        solid = Image.new("RGBA", rgba.size, (*color, 255))
        solid.putalpha(rgba.getchannel("A"))
        return solid

    def _draw_sr_badge_centered(self, img, draw, cx: int, cy: int,
                                sr: float) -> None:
        col = _sr_color(sr)
        # osu!-style legibility: dark glyphs on bright fills, white on dark
        # ones, picked by the badge's luminance (yellow/green/cyan need dark
        # text; red/magenta/indigo/navy need white).
        lum = 0.299 * col[0] + 0.587 * col[1] + 0.114 * col[2]
        fg = (20, 20, 24) if lum > 150 else (255, 255, 255)
        text = f"{sr:.2f}"
        star = _white_icon(load_icon("star", size=14))
        if star:
            star = self._tint_icon(star, fg)
        tw, th = self._text_size(draw, text, self.font_label)
        sw = (star.width + 3) if star else 0
        pad_x, pad_y = 9, 4
        w = sw + tw + pad_x * 2
        h = th + pad_y * 2
        x = cx - w // 2
        y = cy - h // 2
        self._aa_rounded_fill(img, (x, y, x + w, y + h),
                              radius=h // 2, fill=col)
        ix = x + pad_x
        if star:
            img.paste(star, (ix, y + (h - star.height) // 2), star)
            ix += star.width + 3
        d = ImageDraw.Draw(img)
        self._draw_text(d, (ix, y + pad_y - 3), text, self.font_label, fg)

    def _draw_mini_stat(self, img, draw, x: int, y: int, w: int,
                        label: str, value) -> None:
        """Compact CS/AR/OD/HP row sized for one half of a card body."""
        v = float(value or 0.0)
        frac = max(0.0, min(1.0, v / 10.0))
        self._draw_text(draw, (x, y), label, self.font_stat_label,
                        TEXT_SECONDARY)
        val_str = f"{v:g}"
        self._draw_text(draw, (x + 24, y), val_str, self.font_stat_label,
                        _WHITE)
        bx0 = x + 50
        bx1 = x + w
        bh = 5
        by = y + 5
        if bx1 - bx0 > bh:
            self._aa_rounded_fill(img, (bx0, by, bx1, by + bh),
                                  radius=bh // 2, fill=_BAR_TRACK)
            fill_w = int((bx1 - bx0) * frac)
            if fill_w >= bh:
                self._aa_rounded_fill(img, (bx0, by, bx0 + fill_w, by + bh),
                                      radius=bh // 2, fill=_BAR_FILL)

    def _fit_pool(self, draw, text, font, max_w) -> str:
        if not text:
            return text
        if self._text_size(draw, text, font)[0] <= max_w:
            return text
        t = text
        while t and self._text_size(draw, t + "…", font)[0] > max_w:
            t = t[:-1]
        return (t + "…") if t else text

    async def generate_duel_pool_card_async(self, data: Dict) -> BytesIO:
        maps = (data.get("maps", []) or [])[:6]

        async def _cov(bsid):
            if not bsid:
                return None
            r = await download_image(
                f"https://assets.ppy.sh/beatmaps/{bsid}/covers/cover.jpg"
            )
            return r if (r and not isinstance(r, Exception)) else None

        covers = await asyncio.gather(
            *[_cov(m.get("beatmapset_id")) for m in maps]
        )
        return await asyncio.to_thread(
            self.generate_duel_pool_card, data, list(covers)
        )
