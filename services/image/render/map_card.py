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

from services.image.constants import TEXT_SECONDARY, MOD_COLORS, ACCENT_RED
from services.image.utils import download_image, cover_center_crop, load_icon, load_mod_icon
from services.image.render.recent import _sr_color
from services.image.render.titles import _ink_for
from utils.formatting.text import format_length


def _white_icon(icon):
    """Recolour an icon to solid white, keeping its alpha silhouette.
    (Moved here from the removed duel_pool_card.py — map cards were its
    last remaining consumer.)"""
    if icon is None:
        return None
    icon = icon.convert("RGBA")
    solid = Image.new("RGBA", icon.size, (255, 255, 255, 255))
    solid.putalpha(icon.getchannel("A"))
    return solid


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

# The `map` what-if card's nested-tile background (stat cells, graph panel,
# mods panel, PP-by-accuracy panel) — a shade lighter than the card's own
# _PANEL so they read as distinct tiles without a border.
_WHATIF_CELL = (28, 30, 42)
_WHATIF_MUTED = (150, 150, 168)
_WHATIF_BAR = (100, 140, 230)          # CS/AR/OD/HP progress-bar fill
# Difficulty-relevant mods shown on the what-if card's mod row. DT is folded
# into NC's slot (both are the speed-up bucket) rather than given its own —
# see _whatif_mods_row.
_WHATIF_MOD_SET = ("EZ", "HD", "HR", "NC", "FL")


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

    # ── helpers inherited from the removed DuelPoolCardMixin ────────────────
    # (map cards were their last remaining consumer)

    def _tint_icon(self, icon: Image.Image, color: tuple) -> Image.Image:
        """Recolour a white-silhouette icon to `color`, keeping its alpha."""
        rgba = icon.convert("RGBA")
        solid = Image.new("RGBA", rgba.size, (*color, 255))
        solid.putalpha(rgba.getchannel("A"))
        return solid

    def _tint_or_none(self, icon, color: tuple):
        """`_tint_icon`, but passes a missing icon (asset not found) through
        as None instead of raising — every what-if-card icon call site can
        stay a one-liner even if a glyph is absent."""
        return self._tint_icon(icon, color) if icon is not None else None

    def _fit_pool(self, draw, text, font, max_w) -> str:
        if not text:
            return text
        if self._text_size(draw, text, font)[0] <= max_w:
            return text
        t = text
        while t and self._text_size(draw, t + "…", font)[0] > max_w:
            t = t[:-1]
        return (t + "…") if t else text

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

    def _draw_identity_header(self, card, data: Dict, zone_h: int, cover) -> None:
        """Cover+gradient background / status pill / SR badge / title-artist-
        mapper block, confined to the top `zone_h` px of `card`. Shared by
        generate_map_card (zone_h = the whole card) and generate_whatif_card
        (zone_h = just its top identity strip). No rounded-corner masking
        happens here — each caller composites `card` through its own final
        rounded mask, so an unmasked flat paste here is corrected either way."""
        w = card.width
        if cover is not None:
            try:
                bg = cover_center_crop(cover.convert("RGBA"), w, zone_h)
                bg = Image.alpha_composite(bg, _vertical_shade(w, zone_h, 60, 225))
                card.paste(bg, (0, 0), bg)
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

        # ── Title / artist / mapper block, lifted off the bottom of the zone ─
        text_w = w - _PAD * 2
        artist = self._fit_pool(draw, str(data.get("artist") or ""),
                                self.font_subtitle, text_w)
        title = self._fit_pool(draw, str(data.get("title") or "???"),
                               self.font_big, text_w)
        creator = str(data.get("creator") or "")
        version = str(data.get("version") or "")
        meta = f"[{version}]" + (f"  ·  mapped by {creator}" if creator else "")
        meta = self._fit_pool(draw, meta, self.font_label, text_w)

        self._draw_text(draw, (_PAD, zone_h - 150), artist, self.font_subtitle,
                        TEXT_SECONDARY, shadow=True)
        self._draw_text(draw, (_PAD, zone_h - 126), title, self.font_big,
                        _WHITE, shadow=True)
        self._draw_text(draw, (_PAD, zone_h - 86), meta, self.font_label,
                        (170, 178, 215), shadow=True)

    def generate_map_card(
        self, data: Dict, cover: Optional[Image.Image] = None,
    ) -> BytesIO:
        w, h = _W, _H
        card = Image.new("RGBA", (w, h), (0, 0, 0, 0))

        # ── Body ──────────────────────────────────────────────────────────────
        self._aa_rounded_fill(card, (0, 0, w, h), radius=_RADIUS, fill=_PANEL)
        mask = self._rounded_mask((w, h), _RADIUS)
        self._draw_identity_header(card, data, h, cover)
        draw = ImageDraw.Draw(card)

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

    def _whatif_chip(self, img, x, y, w, h, value, *, icon=None,
                     fill=_WHATIF_CELL, value_color=_WHITE) -> None:
        """A compact icon+value tile (SR/BPM/length/combo row) — no text
        label, just the canonical icon and a small value, centred as a
        group."""
        self._aa_rounded_fill(img, (x, y, x + w, y + h), radius=12, fill=fill)
        draw = ImageDraw.Draw(img)
        tw = self._text_size(draw, value, self.font_stat_label)[0]
        iw = (icon.width + 6) if icon else 0
        ix = x + (w - iw - tw) // 2
        cy = y + h // 2
        if icon:
            img.paste(icon, (ix, cy - icon.height // 2), icon)
            ix += icon.width + 6
        self._draw_text(draw, (ix, self._tt_cy(value, self.font_stat_label, cy)),
                        value, self.font_stat_label, value_color)

    def _whatif_stat_bar(self, img, x, y, w, h, icon, value: float, *, max_value: float = 10.0) -> None:
        """CS/AR/OD/HP row: canonical icon + value, with a fill bar showing
        value/max_value beneath — a compact alternative to a bare number."""
        self._aa_rounded_fill(img, (x, y, x + w, y + h), radius=12, fill=_WHATIF_CELL)
        draw = ImageDraw.Draw(img)
        val_txt = f"{value:.1f}"
        ix = x + 12
        cy = y + 17
        if icon:
            img.paste(icon, (ix, cy - icon.height // 2), icon)
            ix += icon.width + 6
        self._draw_text(draw, (ix, self._tt_cy(val_txt, self.font_stat_label, cy)),
                        val_txt, self.font_stat_label, _WHITE)

        bar_x0, bar_x1 = x + 12, x + w - 12
        bar_y, bar_h = y + h - 12, 5
        self._aa_rounded_fill(img, (bar_x0, bar_y, bar_x1, bar_y + bar_h), radius=bar_h // 2, fill=(48, 50, 66))
        frac = max(0.0, min(1.0, value / max_value)) if max_value else 0.0
        fill_x1 = int(bar_x0 + (bar_x1 - bar_x0) * frac)
        if fill_x1 - bar_x0 >= bar_h:
            self._aa_rounded_fill(img, (bar_x0, bar_y, fill_x1, bar_y + bar_h), radius=bar_h // 2, fill=_WHATIF_BAR)

    def _whatif_mods_row(self, img, x, y, w, mods_str: str) -> None:
        """МОДЫ section: the current mod combo as small icon badges
        (top-right, same style as _tt_mod_pill elsewhere), then the 5
        difficulty-relevant mods as ICON-ONLY pills below — filled+coloured
        when active (DT lights up NC's slot — both are the speed-up
        bucket), outlined+dim otherwise."""
        tokens = [mods_str[i:i + 2] for i in range(0, len(mods_str), 2)]
        highlight = set(tokens)
        if "DT" in highlight:
            highlight.add("NC")

        draw = ImageDraw.Draw(img)
        self._draw_text(draw, (x, y + 6), "МОДЫ", self.font_label, _WHATIF_MUTED)

        if tokens:
            # Badge width matches _tt_mod_pill's own geometry (gly=27, w=31)
            # so this precomputed total lines up with what it actually draws.
            badge_w = 31
            total_w = len(tokens) * badge_w + (len(tokens) - 1) * 6
            bx = x + w - total_w
            for m in tokens:
                bx = self._tt_mod_pill(img, bx, y + 6, m) + 6
        else:
            self._text_right(draw, x + w, y + 6, "NM", self.font_label, (230, 190, 90))

        row_y = y + 40
        row_h = 52
        gap = 10
        n = len(_WHATIF_MOD_SET)
        cell_w = (w - (n - 1) * gap) / n
        cx = x
        for m in _WHATIF_MOD_SET:
            is_active = m in highlight
            col = MOD_COLORS.get(m, (110, 110, 130))
            x0, x1 = int(cx), int(cx + cell_w)
            if is_active:
                self._aa_rounded_fill(img, (x0, row_y, x1, row_y + row_h), radius=10, fill=col)
                ink = _ink_for(col)
            else:
                self._aa_rounded_outline(img, (x0, row_y, x1, row_y + row_h),
                                         radius=10, outline=(70, 70, 88), width=1)
                ink = (150, 150, 165)
            icon = load_mod_icon(m, size=22)
            if icon:
                icon = self._tint_icon(icon, ink)
                img.paste(icon, ((x0 + x1) // 2 - icon.width // 2, row_y + (row_h - icon.height) // 2), icon)
            cx += cell_w + gap

    def _whatif_pp_brackets(self, img, x, y, w, accuracy: float, pp: float,
                            brackets: Dict[float, float]) -> None:
        """PP ЗА ТОЧНОСТЬ section: the queried accuracy as a small readout
        top-right, then PP at each reference accuracy milestone in its own
        bordered panel. Whichever milestone sits within 0.5% of the queried
        accuracy is REPLACED by the exact queried value and highlighted red
        — the rest stay grey. If nothing is that close, all four show their
        plain milestone values (still useful context; the corner readout
        still carries the exact number either way)."""
        draw = ImageDraw.Draw(img)
        self._draw_text(draw, (x, y), "PP ЗА ТОЧНОСТЬ", self.font_label, _WHATIF_MUTED)

        acc_txt = f"{accuracy:.0f}"
        box_w = 64
        box_h = 30
        bx1, bx0 = x + w, x + w - box_w
        by0 = y - 4
        self._aa_rounded_fill(img, (bx0, by0, bx1, by0 + box_h), radius=8, fill=(20, 21, 30))
        draw = ImageDraw.Draw(img)
        self._draw_text(draw, (bx0 + 12, self._tt_cy(acc_txt, self.font_stat_label, by0 + box_h // 2)),
                        acc_txt, self.font_stat_label, _WHITE)
        self._draw_text(draw, (bx1 - 20, self._tt_cy("%", self.font_small, by0 + box_h // 2)),
                        "%", self.font_small, _WHATIF_MUTED)

        items = sorted(brackets.items())
        if not items:
            return
        nearest_pct = min(brackets, key=lambda p: abs(p - accuracy))
        swap = abs(nearest_pct - accuracy) <= 0.5

        row_y = y + 40
        row_h = 68
        gap = 12
        n = len(items)
        cell_w = (w - (n - 1) * gap) / n
        cx = x
        for pct, bracket_pp in items:
            is_custom = swap and pct == nearest_pct
            show_pct = accuracy if is_custom else pct
            show_pp = pp if is_custom else bracket_pp
            col = ACCENT_RED if is_custom else (70, 70, 88)
            txt_col = ACCENT_RED if is_custom else _WHATIF_MUTED
            x0, x1 = int(cx), int(cx + cell_w)
            self._aa_rounded_outline(img, (x0, row_y, x1, row_y + row_h), radius=12, outline=col, width=2)
            draw = ImageDraw.Draw(img)
            self._text_center(draw, (x0 + x1) // 2, row_y + 12, f"{show_pct:.0f}%", self.font_stat_label, txt_col)
            self._text_center(draw, (x0 + x1) // 2, row_y + row_h - 26, f"{show_pp:.0f}",
                              self.font_stat_value, _WHITE)
            cx += cell_w + gap

    def generate_whatif_card(
        self, data: Dict, cover: Optional[Image.Image] = None, strains: Optional[list] = None,
    ) -> BytesIO:
        """The `map` command's result: "what if I played this map at X%
        accuracy with these mods" — not a real play. A thumbnail header,
        SR/BPM/length/combo + CS/AR/OD/HP stat grids, the map's own strain
        graph (reused from recent.py), which mods are active, and PP at the
        queried accuracy alongside reference milestones (95/98/99/100%)."""
        w = _W
        inner_w = w - _PAD * 2

        thumb_sz = 88
        head_h = thumb_sz + 28
        row_h = 46
        graph_h = 130
        mods_h = 104
        ppacc_h = 140

        row1_y = _PAD + head_h + 16
        row2_y = row1_y + row_h + 12
        graph_y = row2_y + row_h + 16
        mods_y = graph_y + graph_h + 16
        ppacc_y = mods_y + mods_h + 16
        h = ppacc_y + ppacc_h + _PAD

        card = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
        self._aa_rounded_fill(card, (0, 0, w, h), radius=_RADIUS, fill=_PANEL)
        mask = self._rounded_mask((int(w), int(h)), _RADIUS)
        draw = ImageDraw.Draw(card)

        # ── Header: thumbnail + artist/title/version/mapper, status pill ────
        thumb_x, thumb_y = _PAD, _PAD
        if cover is not None:
            try:
                thumb = cover_center_crop(cover.convert("RGBA"), thumb_sz, thumb_sz)
                tmask = self._rounded_mask((thumb_sz, thumb_sz), 14)
                card.paste(thumb, (thumb_x, thumb_y), tmask)
            except Exception:
                pass
        else:
            self._aa_rounded_fill(card, (thumb_x, thumb_y, thumb_x + thumb_sz, thumb_y + thumb_sz),
                                  radius=14, fill=(40, 42, 56))

        text_x = thumb_x + thumb_sz + 16
        text_w = w - text_x - _PAD - 110  # leave room for the status pill
        artist = self._fit_pool(draw, str(data.get("artist") or ""), self.font_subtitle, text_w)
        title = self._fit_pool(draw, str(data.get("title") or "???"), self.font_big, text_w)
        self._draw_text(draw, (text_x, thumb_y), artist, self.font_subtitle, TEXT_SECONDARY)
        self._draw_text(draw, (text_x, thumb_y + 22), title, self.font_big, _WHITE)
        version = str(data.get("version") or "")
        creator = str(data.get("creator") or "")
        vtxt = f"[{version}]" if version else ""
        vtw = self._text_size(draw, vtxt, self.font_label)[0] if vtxt else 0
        self._draw_text(draw, (text_x, thumb_y + 62), vtxt, self.font_label, (230, 190, 90))
        if creator:
            self._draw_text(draw, (text_x + vtw + 10, thumb_y + 62), creator, self.font_label, _WHATIF_MUTED)

        status = (data.get("status") or "").upper()
        if status:
            scol = _status_pill_color(data.get("status"))
            stw, sth = self._text_size(draw, status, self.font_stat_label)
            spx, spy = 10, 5
            sx1 = w - _PAD
            sx0 = sx1 - (stw + spx * 2)
            self._aa_rounded_fill(card, (sx0, _PAD, sx1, _PAD + sth + spy * 2),
                                  radius=(sth + spy * 2) // 2, fill=scol)
            draw = ImageDraw.Draw(card)
            draw.text((sx0 + spx, _PAD + spy - 1), status, font=self.font_stat_label, fill=(15, 15, 18))
            draw = ImageDraw.Draw(card)

        # ── Row 1: SR / BPM / length / combo — compact icon+value chips ──────
        sr = float(data.get("star_rating") or 0.0)
        gap = 12
        cell_w = (inner_w - gap * 3) / 4
        star = self._tint_or_none(_white_icon(load_icon("star", size=14)), _WHITE)
        bpm_icon = self._tint_or_none(_white_icon(load_icon("bpm", size=14)), _WHATIF_MUTED)
        timer_icon = self._tint_or_none(_white_icon(load_icon("timer", size=14)), _WHATIF_MUTED)
        combo_icon = self._tint_or_none(_white_icon(load_icon("combo", size=14)), _WHATIF_MUTED)
        cx = _PAD
        self._whatif_chip(card, int(cx), row1_y, int(cell_w), row_h, f"{sr:.2f}",
                          icon=star, fill=_sr_color(sr), value_color=_WHITE)
        cx += cell_w + gap
        self._whatif_chip(card, int(cx), row1_y, int(cell_w), row_h,
                          str(int(round(float(data.get("bpm") or 0)))), icon=bpm_icon)
        cx += cell_w + gap
        self._whatif_chip(card, int(cx), row1_y, int(cell_w), row_h,
                          format_length(data.get("length")), icon=timer_icon)
        cx += cell_w + gap
        self._whatif_chip(card, int(cx), row1_y, int(cell_w), row_h,
                          f"{int(data.get('max_combo') or 0)}x", icon=combo_icon)

        # ── Row 2: CS / AR / OD / HP — canonical icon + value + progress bar ──
        cx = _PAD
        for key, icon_name, max_v in (("cs", "cs", 10.0), ("ar", "ar", 10.0),
                                       ("od", "od", 10.0), ("hp_drain", "hp", 10.0)):
            icon = self._tint_or_none(_white_icon(load_icon(icon_name, size=14)), _WHATIF_MUTED)
            self._whatif_stat_bar(card, int(cx), row2_y, int(cell_w), row_h,
                                  icon, float(data.get(key) or 0.0), max_value=max_v)
            cx += cell_w + gap

        # ── Strain graph (reused from RecentCardMixin) ───────────────────────
        self._aa_rounded_fill(card, (_PAD, graph_y, w - _PAD, graph_y + graph_h), radius=14, fill=_WHATIF_CELL)
        self._draw_perf_graph(
            card, _PAD + 16, graph_y + 12, inner_w - 32, graph_h - 40,
            strains or [], 1.0, True, self.font_stat_label,
            {"no_data": "НЕТ ДАННЫХ", "failed": "ФЕЙЛ"},
        )

        # ── Mods ──────────────────────────────────────────────────────────────
        self._aa_rounded_fill(card, (_PAD, mods_y, w - _PAD, mods_y + mods_h), radius=14, fill=_WHATIF_CELL)
        self._whatif_mods_row(card, _PAD + 16, mods_y + 14, inner_w - 32, str(data.get("mods") or ""))

        # ── PP by accuracy ────────────────────────────────────────────────────
        self._aa_rounded_fill(card, (_PAD, ppacc_y, w - _PAD, ppacc_y + ppacc_h), radius=14, fill=_WHATIF_CELL)
        brackets = data.get("brackets") or {}
        self._whatif_pp_brackets(card, _PAD + 16, ppacc_y + 14, inner_w - 32,
                                 float(data.get("accuracy") or 0.0), float(data.get("pp") or 0.0), brackets)

        out = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
        out.paste(card, (0, 0), mask)
        return self._save(out.convert("RGB"))

    async def generate_whatif_card_async(self, data: Dict) -> BytesIO:
        from utils.osu.pp_calculator import calculate_strains

        cover = None
        url = data.get("cover_url")
        if not url and data.get("beatmapset_id"):
            url = (f"https://assets.ppy.sh/beatmaps/"
                   f"{data['beatmapset_id']}/covers/cover@2x.jpg")
        if url:
            r = await download_image(url)
            cover = r if (r and not isinstance(r, Exception)) else None
        strains = None
        beatmap_id = data.get("beatmap_id")
        if beatmap_id:
            try:
                strains = await calculate_strains(beatmap_id, str(data.get("mods") or ""))
            except Exception:
                strains = None
        return self.generate_whatif_card(data, cover, strains)

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
