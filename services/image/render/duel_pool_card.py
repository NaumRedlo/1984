"""Duel map-pool card — a 2×3 grid of map tiles.

Sent to both players (in DM) when a duel is accepted: the full auto-built pool
they will play. Each tile shows the cover, an SR badge coloured by difficulty,
title / mapper / diff, length + max-combo, and CS/AR/OD/HP bars. No skill-type
tag — the per-axis classifier was removed; star_rating is the only difficulty
signal.
"""

import asyncio
from io import BytesIO
from typing import Dict, List, Optional

from PIL import Image, ImageDraw

from services.image.constants import (
    TEXT_PRIMARY, TEXT_SECONDARY, ACCENT_RED, PADDING_X,
)
from services.image.utils import download_image, cover_center_crop, load_icon


# SR → colour ramp (green → blue → gold → pink → purple). 7–10★ lands in the
# pink-purple band, matching the reference card.
_SR_STOPS = [
    (1.5, (102, 204, 102)),
    (3.0, (79, 192, 255)),
    (4.5, (84, 145, 255)),
    (5.5, (255, 204, 70)),
    (6.5, (255, 120, 95)),
    (7.5, (236, 92, 142)),
    (8.5, (201, 100, 222)),
    (10.0, (168, 88, 232)),
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


# Tile geometry
_M = 16          # outer margin
_GAP = 16        # gap between tiles
_COLS = 2
_COVER_H = 132
_TILE_H = 306
_PAD = 14        # inner padding
_RADIUS = 12

_PANEL = (22, 24, 34)
_BAR_TRACK = (44, 46, 60)
_BAR_FILL = (224, 78, 92)


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

        W = 800
        tile_w = (W - 2 * _M - _GAP) // _COLS
        header_h = 46
        sub_h = 30
        grid_top = header_h + sub_h + 8
        rows = (len(maps) + _COLS - 1) // _COLS if maps else 1
        H = grid_top + rows * _TILE_H + (rows - 1) * _GAP + _M

        img, draw = self._create_canvas(W, H)

        # ── Header — centred title ────────────────────────────────────────────
        draw.rectangle([(0, 0), (W, header_h)], fill=(18, 18, 28))
        self._text_center(draw, W // 2, 8, "DUEL · MAP POOL", self.font_big, ACCENT_RED, shadow=True)
        draw.line((0, header_h - 1, W, header_h - 1), fill=(40, 40, 55))

        # ── Sub-header: format (left) + target difficulty (right) ─────────────
        sub_y = header_h + (sub_h - 18) // 2
        fmt = f"{mode_label} · Bo{total_rounds} · TO {win_target}" if total_rounds else mode_label
        self._draw_text(draw, (PADDING_X, sub_y), fmt, self.font_label, TEXT_SECONDARY)
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
            self._draw_text(draw, (sx, sub_y + 5), tgt, self.font_label, TEXT_PRIMARY)

        # ── Tiles ────────────────────────────────────────────────────────────
        for i, m in enumerate(maps):
            col = i % _COLS
            row = i // _COLS
            tx = _M + col * (tile_w + _GAP)
            ty = grid_top + row * (_TILE_H + _GAP)
            cov = covers[i] if i < len(covers) else None
            draw = self._draw_tile(img, draw, tx, ty, tile_w, m, cov, i + 1)

        return self._save(img)

    # ── one tile ─────────────────────────────────────────────────────────────

    def _draw_tile(self, img, draw, tx: int, ty: int, tile_w: int, m: Dict,
                   cover: Optional[Image.Image], index: int):
        # Panel background (all corners rounded).
        self._aa_rounded_fill(img, (tx, ty, tx + tile_w, ty + _TILE_H),
                              radius=_RADIUS, fill=_PANEL)

        # Cover band with rounded top corners + darkening overlay.
        mask = self._rounded_mask((tile_w, _COVER_H), _RADIUS)
        md = ImageDraw.Draw(mask)
        md.rectangle((0, _RADIUS, tile_w, _COVER_H), fill=255)
        if cover:
            try:
                thumb = cover_center_crop(cover.convert("RGBA"), tile_w, _COVER_H)
                ov = Image.new("RGBA", (tile_w, _COVER_H), (0, 0, 0, 70))
                thumb = Image.alpha_composite(thumb, ov).convert("RGB")
                img.paste(thumb, (tx, ty), mask)
            except Exception:
                pass
        draw = ImageDraw.Draw(img)

        # SR badge — top-right, coloured by SR.
        sr = float(m.get("star_rating") or 0.0)
        self._draw_sr_badge(img, draw, tx + tile_w - 10, ty + 10, sr)

        # Index disc — top-left over the cover.
        r = 14
        ix0, iy0 = tx + 10, ty + 10
        self._aa_ellipse_fill(img, (ix0, iy0, ix0 + 2 * r, iy0 + 2 * r), fill=(74, 64, 104))
        draw = ImageDraw.Draw(img)
        # Centre the digit in the disc both ways (textbbox handles the vertical
        # bearing so it sits dead-centre, not baseline-aligned).
        idx_str = str(index)
        bb = draw.textbbox((0, 0), idx_str, font=self.font_stat_label)
        idx_top = (iy0 + r) - (bb[1] + bb[3]) / 2 + 2
        self._text_center(draw, ix0 + r, int(round(idx_top)), idx_str, self.font_stat_label, (235, 235, 245))

        # Title + (artist | mapper) on the left; length / combo / BPM on the
        # right — values bold & white.
        cy = ty + _COVER_H + 12
        left_x = tx + _PAD
        right_x = tx + tile_w - _PAD
        vfont = self.font_label
        vcol = (255, 255, 255)
        length_str = _fmt_len(m.get("length"))
        combo = int(m.get("max_combo") or 0)
        combo_str = f"{combo}×" if combo else "—"
        bpm = int(round(float(m.get("bpm") or 0)))
        bpm_str = str(bpm) if bpm else "—"
        clock = _white_icon(load_icon("timer", size=15))
        combo_icon = _white_icon(load_icon("combo", size=15))
        bpm_icon = _white_icon(load_icon("bpm", size=15))

        # Values are left-aligned in a shared column so they line up under the
        # icons; each icon's right edge sits a fixed gap before that column.
        _gap = 5
        _vdy = 4  # nudge the value column a touch lower
        rows_rc = [(cy + _vdy, length_str, clock),
                   (cy + 24 + _vdy, combo_str, combo_icon),
                   (cy + 48 + _vdy, bpm_str, bpm_icon)]
        max_val_w = max(self._text_size(draw, t, vfont)[0] for _, t, _ in rows_rc)
        max_icon_w = max((ic.width for _, _, ic in rows_rc if ic), default=0)
        value_x = right_x - max_val_w
        for yy, text, icon in rows_rc:
            if icon:
                img.paste(icon, (value_x - _gap - icon.width, yy + 3), icon)
            self._draw_text(ImageDraw.Draw(img), (value_x, yy), text, vfont, vcol)
        draw = ImageDraw.Draw(img)

        text_max = (value_x - _gap - max_icon_w - 12) - left_x

        title = self._fit_pool(draw, str(m.get("title") or "???"), self.font_row, text_max)
        self._draw_text(draw, (left_x, cy - 2), title, self.font_row, TEXT_PRIMARY)
        artist = str(m.get("artist") or "")
        mapper = str(m.get("creator") or "")
        am = f"{artist} | {mapper}" if (artist and mapper) else (artist or mapper)
        am = self._fit_pool(draw, am, self.font_small, text_max)
        if am:
            self._draw_text(draw, (left_x, cy + 26), am, self.font_small, TEXT_SECONDARY)

        # Difficulty name (no type pill).
        diff_y = cy + 50
        diff = self._fit_pool(draw, str(m.get("version") or "?"), self.font_label, tile_w - 2 * _PAD)
        self._draw_text(draw, (tx + _PAD, diff_y), diff, self.font_label, (150, 160, 200))

        # CS / AR / OD / HP bars — tight rows, bar centred on each value.
        bar_y = diff_y + 30
        for label, key in (("CS", "cs"), ("AR", "ar"), ("OD", "od"), ("HP", "hp_drain")):
            self._draw_stat_bar(img, draw, tx, bar_y, tile_w, label, m.get(key))
            draw = ImageDraw.Draw(img)
            bar_y += 20
        return draw

    def _draw_sr_badge(self, img, draw, x_right: int, y: int, sr: float) -> None:
        col = _sr_color(sr)
        text = f"{sr:.2f}"
        star = _white_icon(load_icon("star", size=14))
        tw, th = self._text_size(draw, text, self.font_label)
        sw = (star.width + 3) if star else 0
        pad_x, pad_y = 9, 4
        w = sw + tw + pad_x * 2
        h = th + pad_y * 2
        x = x_right - w
        self._aa_rounded_fill(img, (x, y, x + w, y + h), radius=h // 2, fill=col)
        ix = x + pad_x
        if star:
            img.paste(star, (ix, y + (h - star.height) // 2), star)
            ix += star.width + 3
        d = ImageDraw.Draw(img)
        self._draw_text(d, (ix, y + pad_y - 3), text, self.font_label, (255, 255, 255))

    def _draw_stat_bar(self, img, draw, tx: int, y: int, tile_w: int,
                       label: str, value) -> None:
        v = float(value or 0.0)
        frac = max(0.0, min(1.0, v / 10.0))
        self._draw_text(draw, (tx + _PAD, y), label, self.font_stat_label, TEXT_SECONDARY)
        val_str = f"{v:g}"
        self._draw_text(draw, (tx + _PAD + 32, y), val_str, self.font_stat_label, TEXT_PRIMARY)

        bx0 = tx + _PAD + 70
        bx1 = tx + tile_w - _PAD
        bh = 7
        by = y + 4   # vertically centred against the CS/AR/OD/HP value text
        self._aa_rounded_fill(img, (bx0, by, bx1, by + bh), radius=bh // 2, fill=_BAR_TRACK)
        fill_w = int((bx1 - bx0) * frac)
        if fill_w >= bh:
            self._aa_rounded_fill(img, (bx0, by, bx0 + fill_w, by + bh), radius=bh // 2, fill=_BAR_FILL)

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
            r = await download_image(f"https://assets.ppy.sh/beatmaps/{bsid}/covers/cover.jpg")
            return r if (r and not isinstance(r, Exception)) else None

        covers = await asyncio.gather(*[_cov(m.get("beatmapset_id")) for m in maps])
        return await asyncio.to_thread(self.generate_duel_pool_card, data, list(covers))
