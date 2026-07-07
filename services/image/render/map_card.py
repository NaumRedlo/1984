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
from utils.osu.mod_utils import WHATIF_MOD_SET


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
# Nested tiles inside an already-_WHATIF_CELL-coloured section panel (the PP-
# by-accuracy brackets) need a darker shade of their own, or they'd blend
# into the section background and disappear.
_WHATIF_CELL_DARK = (20, 21, 30)
_WHATIF_RED_TEXT = (235, 110, 110)     # pp value inside the highlighted accuracy bracket
_GOLD = (255, 202, 40)                 # SR value/star when the map is ≥ 6.5★


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

    def _whatif_cell(self, img, x, y, w, h, label, value, *,
                     fill=_WHATIF_CELL, value_color=_WHITE, icon=None) -> None:
        """One label-over-value tile — the stat grids (SR/BPM/length/combo,
        then CS/AR/OD/HP) and the PP-by-accuracy brackets are all this same
        shape. The SR cell passes an empty label (the star icon already
        identifies it) and gets its icon+value centred in the full height
        instead of anchored under a label row."""
        self._aa_rounded_fill(img, (x, y, x + w, y + h), radius=14, fill=fill)
        draw = ImageDraw.Draw(img)
        if not label:
            tw = self._text_size(draw, value, self.font_stat_value)[0]
            total_w = (icon.width + 6 if icon else 0) + tw
            vx = x + (w - total_w) // 2
            cy = y + h // 2
            if icon:
                img.paste(icon, (vx, cy - icon.height // 2), icon)
                vx += icon.width + 6
            self._draw_text(draw, (vx, self._tt_cy(value, self.font_stat_value, cy)),
                            value, self.font_stat_value, value_color)
            return
        self._text_center(draw, x + w // 2, y + 14, label, self.font_stat_label, _WHATIF_MUTED)
        if icon:
            tw = self._text_size(draw, value, self.font_stat_value)[0]
            total_w = icon.width + 6 + tw
            vx = x + (w - total_w) // 2
            vy = y + h - 36
            img.paste(icon, (vx, vy + 5), icon)
            draw = ImageDraw.Draw(img)
            self._draw_text(draw, (vx + icon.width + 6, vy), value, self.font_stat_value, value_color)
        else:
            self._text_center(draw, x + w // 2, y + h - 30, value, self.font_stat_value, value_color)

    def _whatif_stat_bar(self, img, x, y, w, h, icon, label, value: float, *, max_value: float = 10.0) -> None:
        """CS/AR/OD/HP cell: icon + label on the left, value on the right,
        with a value/max_value fill bar beneath. The bar ramps green→red
        with the value (harder settings read hotter), not a flat blue."""
        self._aa_rounded_fill(img, (x, y, x + w, y + h), radius=14, fill=_WHATIF_CELL)
        draw = ImageDraw.Draw(img)
        ix = x + 14
        # icon, label and value all vertically centred on the same row line.
        row_cy = y + 20
        if icon:
            img.paste(icon, (ix, row_cy - icon.height // 2), icon)
            ix += icon.width + 8
        self._draw_text(draw, (ix, self._tt_cy(label, self.font_stat_value, row_cy)),
                        label, self.font_stat_value, _WHATIF_MUTED)
        val_txt = f"{value:.1f}"
        self._text_right(draw, x + w - 14, self._tt_cy(val_txt, self.font_stat_value, row_cy),
                         val_txt, self.font_stat_value, _WHITE)

        bar_x0, bar_x1 = x + 14, x + w - 14
        bar_y, bar_h = y + h - 15, 6
        self._aa_rounded_fill(img, (bar_x0, bar_y, bar_x1, bar_y + bar_h), radius=bar_h // 2, fill=(48, 50, 66))
        frac = max(0.0, min(1.0, value / max_value)) if max_value else 0.0
        fill_x1 = int(bar_x0 + (bar_x1 - bar_x0) * frac)
        if fill_x1 - bar_x0 >= bar_h:
            t = frac
            col = (int(90 * (1 - t) + 230 * t), int(200 * (1 - t) + 90 * t), 90)
            self._aa_rounded_fill(img, (bar_x0, bar_y, fill_x1, bar_y + bar_h), radius=bar_h // 2, fill=col)

    def _whatif_mods_row(self, img, x, y, w, mods_str: str) -> None:
        """МОДЫ section: the active combo as icon badges top-right ("NM" text
        if none), then the 5 mods as big icon-only pills below — filled+
        coloured when active, outlined+dim otherwise."""
        tokens = [mods_str[i:i + 2] for i in range(0, len(mods_str), 2)]
        active = set(tokens)

        draw = ImageDraw.Draw(img)
        self._draw_text(draw, (x, y), "МОДЫ", self.font_label, _WHATIF_MUTED)
        badge_cy = y + self.font_label.size // 2
        if tokens:
            badge_w = 31  # matches _tt_mod_pill's own gly+4 geometry
            total_w = len(tokens) * badge_w + (len(tokens) - 1) * 5
            bx = x + w - total_w
            for m in tokens:
                bx = self._tt_mod_pill(img, bx, badge_cy, m) + 5
            draw = ImageDraw.Draw(img)
        else:
            self._text_right(draw, x + w, y, "NM", self.font_label, (230, 190, 90))

        row_y = y + 32
        row_h = 66
        gap = 10
        icon_sz = 44
        n = len(WHATIF_MOD_SET)
        cell_w = (w - (n - 1) * gap) / n
        cx = x
        for m in WHATIF_MOD_SET:
            is_active = m in active
            col = MOD_COLORS.get(m, (110, 110, 130))
            x0, x1 = int(cx), int(cx + cell_w)
            if is_active:
                self._aa_rounded_fill(img, (x0, row_y, x1, row_y + row_h), radius=10, fill=col)
                ink = _ink_for(col)
            else:
                self._aa_rounded_outline(img, (x0, row_y, x1, row_y + row_h),
                                         radius=10, outline=(70, 70, 88), width=1)
                ink = (200, 200, 210)
            icon = load_mod_icon(m, size=icon_sz)
            if icon:
                icon = self._tint_icon(icon, ink)
                img.paste(icon, ((x0 + x1) // 2 - icon.width // 2, row_y + (row_h - icon.height) // 2), icon)
            cx += cell_w + gap

    @staticmethod
    def _whatif_active_bracket(accuracy: float, milestones: list) -> float:
        """Which reference milestone "owns" the current accuracy. Priority
        starts at the top (100%) and a column holds the custom value as
        accuracy is dialled down, only handing off to the milestone below it
        once accuracy comes within 0.5% of that lower one — so e.g. 99.6%
        still shows in the 100% column, but 99.4% shifts to the 99% one."""
        active = milestones[0]
        for i in range(len(milestones) - 1, -1, -1):
            lower = milestones[i - 1] if i > 0 else None
            threshold = (lower + 0.5) if lower is not None else float("-inf")
            if accuracy > threshold:
                active = milestones[i]
                break
        return active

    def _whatif_pp_brackets(self, img, x, y, w, accuracy: float, pp: float,
                            brackets: Dict[float, float]) -> None:
        """PP ЗА ТОЧНОСТЬ section: the queried accuracy as a small readout
        top-right (to tenths), then PP at each reference accuracy milestone
        in its own tile. The milestone that currently owns the accuracy (see
        _whatif_active_bracket) shows the exact queried value+pp, outlined/
        tinted red; the rest show their plain milestone value."""
        draw = ImageDraw.Draw(img)
        self._draw_text(draw, (x, y), "PP ЗА ТОЧНОСТЬ", self.font_label, _WHATIF_MUTED)

        acc_txt = f"{accuracy:.1f}"
        box_w = 84
        box_h = 30
        bx1, bx0 = x + w, x + w - box_w
        by0 = y - 4
        self._aa_rounded_fill(img, (bx0, by0, bx1, by0 + box_h), radius=8, fill=_WHATIF_CELL_DARK)
        draw = ImageDraw.Draw(img)
        self._draw_text(draw, (bx0 + 12, self._tt_cy(acc_txt, self.font_stat_label, by0 + box_h // 2)),
                        acc_txt, self.font_stat_label, _WHITE)
        self._draw_text(draw, (bx1 - 18, self._tt_cy("%", self.font_small, by0 + box_h // 2)),
                        "%", self.font_small, _WHATIF_MUTED)

        items = sorted(brackets.items())
        if not items:
            return
        active_pct = self._whatif_active_bracket(accuracy, [p for p, _ in items])

        row_y = y + 40
        row_h = 68
        gap = 12
        n = len(items)
        cell_w = (w - (n - 1) * gap) / n
        cx = x
        for pct, bracket_pp in items:
            is_custom = pct == active_pct
            pct_txt = f"{accuracy:.1f}%" if is_custom else f"{pct:.0f}%"
            show_pp = pp if is_custom else bracket_pp
            x0, x1 = int(cx), int(cx + cell_w)
            if is_custom:
                self._aa_rounded_outline(img, (x0, row_y, x1, row_y + row_h), radius=12,
                                         outline=ACCENT_RED, width=2, fill=_WHATIF_CELL_DARK)
                pct_col, pp_col = ACCENT_RED, _WHATIF_RED_TEXT
            else:
                self._aa_rounded_fill(img, (x0, row_y, x1, row_y + row_h), radius=12, fill=_WHATIF_CELL_DARK)
                pct_col, pp_col = _WHATIF_MUTED, _WHITE
            draw = ImageDraw.Draw(img)
            self._text_center(draw, (x0 + x1) // 2, row_y + 14, pct_txt, self.font_stat_label, pct_col)
            self._text_center(draw, (x0 + x1) // 2, row_y + 34, f"{show_pp:.0f}", self.font_stat_value, pp_col)
            cx += cell_w + gap

    def generate_whatif_card(
        self, data: Dict, cover: Optional[Image.Image] = None, strains: Optional[list] = None,
        mapper_avatar: Optional[Image.Image] = None,
    ) -> BytesIO:
        """The `map` command's result: "what if I played this map at X%
        accuracy with these mods" — not a real play. A thumbnail header,
        SR/BPM/length/combo + CS/AR/OD/HP stat grids, the map's own strain
        graph (reused from recent.py), which mods are active, and PP at the
        queried accuracy alongside reference milestones (95/98/99/100%)."""
        w = _W
        inner_w = w - _PAD * 2

        thumb_sz = 88
        head_pad = 16
        head_h = thumb_sz + head_pad * 2
        row_h = 50            # SR/BPM/length/combo chips
        row2_h = 62          # CS/AR/OD/HP (label + value + progress bar)
        graph_h = 150
        mods_h = 118
        ppacc_h = 140

        row1_y = _PAD + head_h + 16
        row2_y = row1_y + row_h + 12
        graph_y = row2_y + row2_h + 16
        ppacc_y = graph_y + graph_h + 16
        mods_y = ppacc_y + ppacc_h + 16
        h = mods_y + mods_h + _PAD

        card = Image.new("RGBA", (int(w), int(h)), (0, 0, 0, 0))
        self._aa_rounded_fill(card, (0, 0, w, h), radius=_RADIUS, fill=_PANEL)
        mask = self._rounded_mask((int(w), int(h)), _RADIUS)
        draw = ImageDraw.Draw(card)

        # ── Header panel ─────────────────────────────────────────────────────
        px0, py0 = _PAD, _PAD
        px1 = w - _PAD
        panel_w = px1 - px0
        self._aa_rounded_fill(card, (px0, py0, px1, py0 + head_h), radius=14, fill=_WHATIF_CELL)

        # Cover art bled across the RIGHT half of the panel, fading out toward
        # the left so the text stays readable (same technique as the rs hero).
        if cover is not None:
            try:
                from PIL import ImageChops
                hbg = cover_center_crop(cover.convert("RGBA"), panel_w, head_h)
                hbg = Image.alpha_composite(hbg, Image.new("RGBA", (panel_w, head_h), (0, 0, 0, 120)))
                hfade = Image.new("L", (panel_w, head_h), 0)
                _fd = ImageDraw.Draw(hfade)
                _fs = int(panel_w * 0.50)
                for fx in range(_fs, panel_w):
                    _fd.line([(fx, 0), (fx, head_h)], fill=int(235 * (fx - _fs) / max(1, panel_w - _fs)))
                hfade = ImageChops.multiply(hfade, self._rounded_mask((panel_w, head_h), 14))
                card.paste(hbg.convert("RGB"), (px0, py0), hfade)
            except Exception:
                pass
        draw = ImageDraw.Draw(card)

        # Cover thumbnail (left) — wide landscape rectangle.
        cov_w, cov_h = 176, thumb_sz
        thumb_x, thumb_y = px0 + head_pad, py0 + head_pad
        if cover is not None:
            try:
                thumb = cover_center_crop(cover.convert("RGBA"), cov_w, cov_h)
                card.paste(thumb.convert("RGB"), (thumb_x, thumb_y), self._rounded_mask((cov_w, cov_h), 12))
            except Exception:
                pass
        else:
            self._aa_rounded_fill(card, (thumb_x, thumb_y, thumb_x + cov_w, thumb_y + cov_h),
                                  radius=12, fill=(40, 42, 56))
        draw = ImageDraw.Draw(card)

        # Mapper block — pinned to the panel's bottom-right corner: "mapped by"
        # over the name, then the avatar in a red ring.
        creator = str(data.get("creator") or "")
        av_sz = 44
        av_x = px1 - head_pad - av_sz
        av_y = py0 + head_h - head_pad - av_sz
        av_cy = av_y + av_sz // 2
        mapper_left = px1 - head_pad  # updated below to where the mapper block starts
        if creator:
            by_w = self._text_size(draw, "mapped by", self.font_stat_label)[0]
            name_w = self._text_size(draw, creator, self.font_label)[0]
            block_w = max(by_w, name_w)
            text_right = av_x - 12
            if mapper_avatar is not None:
                circle = self._circle_crop(mapper_avatar, av_sz)
                card.paste(circle, (av_x, av_y), circle)
            else:
                self._aa_rounded_fill(card, (av_x, av_y, av_x + av_sz, av_y + av_sz),
                                      radius=av_sz // 2, fill=(60, 62, 80))
            self._aa_ellipse_outline(card, (av_x - 2, av_y - 2, av_x + av_sz + 2, av_y + av_sz + 2),
                                     outline=ACCENT_RED, width=3)
            draw = ImageDraw.Draw(card)
            self._text_right(draw, text_right, av_cy - 20, "mapped by", self.font_stat_label,
                             _WHATIF_MUTED, shadow=True)
            self._text_right(draw, text_right, av_cy - 1, creator, self.font_label, _WHITE, shadow=True)
            mapper_left = text_right - block_w - 14

        text_x = thumb_x + cov_w + 18
        text_y = thumb_y
        text_w = px1 - head_pad - 110 - text_x  # leave room for the status pill
        artist = self._fit_pool(draw, str(data.get("artist") or ""), self.font_subtitle, text_w)
        title = self._fit_pool(draw, str(data.get("title") or "???"), self.font_big, text_w)
        self._draw_text(draw, (text_x, text_y), artist, self.font_subtitle, TEXT_SECONDARY, shadow=True)
        self._draw_text(draw, (text_x, text_y + 22), title, self.font_big, _WHITE, shadow=True)

        # Difficulty name in a pill (rs-style).
        version = str(data.get("version") or "")
        vy = text_y + 64
        if version:
            avail = mapper_left - text_x
            vlabel = version
            while vlabel and self._text_size(draw, vlabel, self.font_stat_label)[0] + 18 > avail and len(vlabel) > 4:
                vlabel = vlabel[:-1]
            if vlabel != version:
                vlabel = vlabel[:-1] + "…"
            vpw = self._text_size(draw, vlabel, self.font_stat_label)[0] + 18
            self._aa_rounded_fill(card, (text_x, vy - 2, text_x + vpw, vy + 22), radius=12, fill=(70, 90, 150))
            draw = ImageDraw.Draw(card)
            self._text_center(draw, text_x + vpw // 2, vy + 2, vlabel, self.font_stat_label, (235, 240, 255))

        # Status pill — top-right corner of the panel, over the faded cover.
        status = (data.get("status") or "").upper()
        if status:
            scol = _status_pill_color(data.get("status"))
            stw, sth = self._text_size(draw, status, self.font_stat_label)
            spx, spy = 10, 5
            sx1 = px1 - head_pad
            sx0 = sx1 - (stw + spx * 2)
            sy0 = py0 + head_pad
            self._aa_rounded_fill(card, (sx0, sy0, sx1, sy0 + sth + spy * 2),
                                  radius=(sth + spy * 2) // 2, fill=scol)
            draw = ImageDraw.Draw(card)
            draw.text((sx0 + spx, sy0 + spy - 1), status, font=self.font_stat_label, fill=(15, 15, 18))
            draw = ImageDraw.Draw(card)

        # ── Row 1: SR / BPM / length / combo — icon + value chips ────────────
        sr = float(data.get("star_rating") or 0.0)
        gap = 12
        cell_w = (inner_w - gap * 3) / 4
        # SR ≥ 6.5 reads gold-with-star (osu's "extra"/high-diff signal);
        # below that it stays white on its difficulty-coloured tile.
        sr_gold = sr >= 6.5
        star_col = _GOLD if sr_gold else _WHITE
        star = self._tint_or_none(_white_icon(load_icon("star", size=20)), star_col)
        bpm_icon = self._tint_or_none(_white_icon(load_icon("bpm", size=20)), _WHITE)
        timer_icon = self._tint_or_none(_white_icon(load_icon("timer", size=20)), _WHITE)
        combo_icon = self._tint_or_none(_white_icon(load_icon("combo", size=20)), _WHITE)
        cx = _PAD
        self._whatif_cell(card, int(cx), row1_y, int(cell_w), row_h, "", f"{sr:.2f}",
                          fill=_sr_color(sr), value_color=(_GOLD if sr_gold else _WHITE), icon=star)
        cx += cell_w + gap
        self._whatif_cell(card, int(cx), row1_y, int(cell_w), row_h, "",
                          str(int(round(float(data.get("bpm") or 0)))), icon=bpm_icon)
        cx += cell_w + gap
        self._whatif_cell(card, int(cx), row1_y, int(cell_w), row_h, "",
                          format_length(data.get("length")), icon=timer_icon)
        cx += cell_w + gap
        self._whatif_cell(card, int(cx), row1_y, int(cell_w), row_h, "",
                          f"{int(data.get('max_combo') or 0)}x", icon=combo_icon)

        # ── Row 2: CS / AR / OD / HP — icon + label + value + progress bar ───
        cx = _PAD
        for label, key, icon_name in (("CS", "cs", "cs"), ("AR", "ar", "ar"),
                                       ("OD", "od", "od"), ("HP", "hp_drain", "hp")):
            icon = self._tint_or_none(_white_icon(load_icon(icon_name, size=22)), _WHITE)
            self._whatif_stat_bar(card, int(cx), row2_y, int(cell_w), row2_h,
                                  icon, label, float(data.get(key) or 0.0))
            cx += cell_w + gap

        # ── Strain graph (reused from RecentCardMixin) ───────────────────────
        self._aa_rounded_fill(card, (_PAD, graph_y, w - _PAD, graph_y + graph_h), radius=14, fill=_WHATIF_CELL)
        gdraw = ImageDraw.Draw(card)
        self._draw_text(gdraw, (_PAD + 16, graph_y + 14), "СЛОЖНОСТЬ КАРТЫ", self.font_label, _WHATIF_MUTED)
        self._draw_perf_graph(
            card, _PAD + 16, graph_y + 44, inner_w - 32, graph_h - 76,
            strains or [], 1.0, True, self.font_stat_label,
            {"no_data": "НЕТ ДАННЫХ", "failed": "ФЕЙЛ"}, show_axis=True,
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
        import asyncio

        from utils.osu.pp_calculator import calculate_strains

        url = data.get("cover_url")
        if not url and data.get("beatmapset_id"):
            url = (f"https://assets.ppy.sh/beatmaps/"
                   f"{data['beatmapset_id']}/covers/cover@2x.jpg")
        mapper_id = data.get("mapper_id")
        avatar_url = f"https://a.ppy.sh/{mapper_id}" if mapper_id else None

        async def _none():
            return None

        cover_r, avatar_r = await asyncio.gather(
            download_image(url) if url else _none(),
            download_image(avatar_url) if avatar_url else _none(),
        )
        cover = cover_r if (cover_r and not isinstance(cover_r, Exception)) else None
        mapper_avatar = avatar_r if (avatar_r and not isinstance(avatar_r, Exception)) else None

        strains = None
        beatmap_id = data.get("beatmap_id")
        if beatmap_id:
            try:
                strains = await calculate_strains(beatmap_id, str(data.get("mods") or ""))
            except Exception:
                strains = None
        return self.generate_whatif_card(data, cover, strains, mapper_avatar)

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
