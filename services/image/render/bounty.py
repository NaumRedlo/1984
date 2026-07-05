"""Bounty card renderers — detail card + list card.

Visual language mirrors the DUEL duel cards: blurred beatmap cover as outer
background, sharp cover inside a rounded hero panel, type/status badges,
typographic section headers.
"""

import asyncio
from io import BytesIO
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFilter

from services.image.constants import (
    BG_COLOR,
    HEADER_BG,
    PANEL_BG,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    ACCENT_GREEN,
)
from services.image.render.duel_pool_card import _sr_color
from services.image.utils import (
    cover_center_crop,
    download_image,
    load_icon,
)
from utils.hp_calculator import BOUNTY_TYPE_MULTIPLIER
from utils.logger import get_logger

logger = get_logger("image.render.bounty")

# Star-rating ranges per tier — shown in tier card header (a star icon is
# pasted before the range; the bundled Torus font has no ★ glyph → tofu).
TIER_SR_RANGES = {
    "C":    "2.0 – 4.5",
    "B":    "4.5 – 7.0",
    "A":    "7.0 – 10.0",
}


def _type_pill_label(btype: str | None) -> str:
    """Return type label with multiplier suffix when T > 1.0 (e.g. 'SS  x1.6')."""
    t = (btype or "First FC").strip()
    mult = BOUNTY_TYPE_MULTIPLIER.get(t, 1.0)
    label = t.upper()
    if mult > 1.0:
        label = f"{label}  x{mult:.1f}"
    return label


BOUNTY_TYPE_COLORS = {
    "first fc":  (200, 140, 50),
    "ss":        (255, 215, 50),
    "accuracy":  (80, 200, 180),
    "metronome": (140, 90, 220),
    "marathon":  (140, 80, 200),
    "mod":       (220, 140, 50),
    "pass":      (100, 160, 220),
}
DEFAULT_TYPE_COLOR = (180, 80, 200)

TIER_COLORS = {
    "C":    (80, 200, 80),
    "B":    (240, 180, 60),
    "A":    (220, 60, 60),
    "Open": (160, 80, 200),
}

# Dark RGBA tint per bounty type — applied over blurred cover as row/card background.
# Subtle enough to keep text readable, distinct enough to visually separate types.
_TYPE_ROW_TINT: dict[str, tuple] = {
    "first fc":  (45, 25,  5, 198),
    "ss":        (45, 38,  2, 198),
    "accuracy":  ( 2, 32, 30, 198),
    "metronome": (22,  8, 45, 198),
    "marathon":  (20,  5, 40, 198),
    "mod":       (42, 22,  2, 198),
    "pass":      ( 5, 20, 45, 198),
}
_DEFAULT_TINT = (6, 6, 14, 200)


def _type_tint(bounty_type: str | None) -> tuple:
    return _TYPE_ROW_TINT.get((bounty_type or "").strip().lower(), _DEFAULT_TINT)


def _strip_difficulty(title: str) -> str:
    """Remove trailing osu! [Difficulty Name] from a beatmap title."""
    if title.endswith("]"):
        idx = title.rfind("[")
        if idx > 0:
            return title[:idx].rstrip()
    return title


def _draw_gradient_divider(img: Image.Image, x: int, y: int,
                            w: int, h: int, c1: tuple, c2: tuple) -> None:
    """Horizontal gradient stripe from c1 to c2, pasted into img at (x, y)."""
    stripe = Image.new("RGB", (w, h))
    px = stripe.load()
    for xi in range(w):
        t = xi / max(w - 1, 1)
        r = int(c1[0] + (c2[0] - c1[0]) * t)
        g = int(c1[1] + (c2[1] - c1[1]) * t)
        b = int(c1[2] + (c2[2] - c1[2]) * t)
        for yi in range(h):
            px[xi, yi] = (r, g, b)
    img.paste(stripe, (x, y))


def _icon_text_inline(
    img: Image.Image,
    draw: ImageDraw.Draw,
    x: int,
    y_center: int,
    icon_name: str,
    icon_size: int,
    text: str,
    font,
    fill,
) -> tuple:
    """Paste `icon_name` (from assets) then draw `text` inline.
    Returns (new_x_after_text, refreshed_draw)."""
    icon = load_icon(icon_name, icon_size)
    if icon:
        img.paste(icon, (x, y_center - icon.height // 2), icon)
        x += icon.width + 4
        draw = ImageDraw.Draw(img)
    bb = draw.textbbox((0, 0), text, font=font)
    ty = y_center - (bb[3] - bb[1]) // 2 - bb[1]
    draw.text((x, ty), text, font=font, fill=fill)
    return x + (bb[2] - bb[0]), draw


MOD_COLORS = {
    "HD": (220, 180, 60),
    "HR": (220, 80, 80),
    "DT": (140, 90, 220),
    "NC": (170, 100, 230),
    "HT": (90, 150, 220),
    "EZ": (100, 200, 100),
    "FL": (60, 60, 80),
    "SD": (210, 110, 60),
    "PF": (210, 80, 130),
    "NF": (120, 130, 150),
    "SO": (180, 180, 80),
    "RX": (200, 80, 200),
    "AP": (200, 80, 200),
    "TD": (160, 160, 160),
}
DEFAULT_MOD_COLOR = (140, 140, 160)


def _split_mods(mods_str: Optional[str]) -> List[str]:
    if not mods_str:
        return []
    s = mods_str.strip().upper().replace(",", "").replace(" ", "")
    return [s[i:i + 2] for i in range(0, len(s) - 1, 2)]


def _type_color(bounty_type: Optional[str]):
    if not bounty_type:
        return DEFAULT_TYPE_COLOR
    return BOUNTY_TYPE_COLORS.get(bounty_type.strip().lower(), DEFAULT_TYPE_COLOR)


class BountyCardMixin:

    # ─────────────────────────────────────────────────────────────────────────
    # COMPACT CARD  (800×256, single bounty, inline nav)
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bounty_compact_card(self, data: Dict) -> BytesIO:
        """Compact 800×256 detail card for a single bounty. Torus-only Latin."""
        W, H = 800, 256
        img = Image.new("RGB", (W, H), BG_COLOR)
        cover = data.get("beatmap_cover")
        btype = data.get("bounty_type") or "First FC"

        # Full-bleed blurred cover + type-tinted overlay
        if cover:
            try:
                bg = cover_center_crop(cover.convert("RGBA"), W, H)
                bg = bg.filter(ImageFilter.GaussianBlur(20))
                tint = _type_tint(btype)
                # Use slightly lighter tint for detail card so cover shows more
                detail_tint = (tint[0], tint[1], tint[2], 200)
                ov = Image.new("RGBA", (W, H), detail_tint)
                bg = Image.alpha_composite(bg, ov)
                img.paste(bg.convert("RGB"), (0, 0))
            except Exception:
                pass

        draw = ImageDraw.Draw(img)

        THUMB_X, THUMB_Y, THUMB_W, THUMB_H = 16, 24, 180, 208
        tier = data.get("tier") or "Open"
        tier_color = TIER_COLORS.get(tier, (160, 80, 200))

        # Tier accent stripe left of thumbnail
        draw.rounded_rectangle(
            (THUMB_X - 6, THUMB_Y + 6, THUMB_X - 3, THUMB_Y + THUMB_H - 6),
            radius=2, fill=tier_color,
        )

        # Cover thumbnail
        if cover:
            try:
                thumb = cover_center_crop(cover.convert("RGBA"), THUMB_W, THUMB_H)
                ov2 = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 60))
                thumb = Image.alpha_composite(thumb, ov2)
                mask = Image.new("L", (THUMB_W, THUMB_H), 0)
                ImageDraw.Draw(mask).rounded_rectangle(
                    (0, 0, THUMB_W - 1, THUMB_H - 1), radius=12, fill=255,
                )
                img.paste(thumb.convert("RGB"), (THUMB_X, THUMB_Y), mask)
                draw = ImageDraw.Draw(img)
            except Exception:
                draw.rounded_rectangle(
                    (THUMB_X, THUMB_Y, THUMB_X + THUMB_W, THUMB_Y + THUMB_H),
                    radius=12, fill=PANEL_BG,
                )
        else:
            draw.rounded_rectangle(
                (THUMB_X, THUMB_Y, THUMB_X + THUMB_W, THUMB_Y + THUMB_H),
                radius=12, fill=PANEL_BG,
            )

        # Right content area
        RX = THUMB_X + THUMB_W + 14   # 210
        RP = W - 14                    # 786
        RW = RP - RX                   # 576

        # ── Row 1: tier pill + type pill   |   bounty id ──────────────────────
        cy = THUMB_Y + 12
        bx = RX
        bx = self._draw_pill(draw, bx, cy, f"TIER {tier}" if tier != "Open" else "OPEN",
                             tier_color, text_fill=(20, 20, 28),
                             font=self.font_stat_label, pad_x=9, pad_y=4, img=img)
        bx += 6
        bx = self._draw_pill(draw, bx, cy, _type_pill_label(btype), _type_color(btype),
                             text_fill=(20, 20, 28), font=self.font_stat_label,
                             pad_x=9, pad_y=4, img=img)

        bid_text = f"#{data.get('bounty_id', '?')}"
        bid_bb = draw.textbbox((0, 0), bid_text, font=self.font_stat_label)
        draw.text((RP - (bid_bb[2] - bid_bb[0]), cy - bid_bb[1]),
                  bid_text, font=self.font_stat_label, fill=TEXT_SECONDARY)

        ref_bb = draw.textbbox((0, 0), "Ag", font=self.font_stat_label)
        pill_h = (ref_bb[3] - ref_bb[1]) + 2 * 4 + 4
        cy += pill_h + 8

        # ── Row 2: beatmap title ──────────────────────────────────────────────
        bt = self._truncate_text(draw, data.get("beatmap_title") or data.get("title") or "—",
                                 self.font_row, RW)
        self._draw_text_shadow(draw, (RX, cy), bt, self.font_row, TEXT_PRIMARY)
        cy += draw.textbbox((0, 0), "Ag", font=self.font_row)[3] + 6

        # ── Row 3: [star] SR   [timer] dur   [account] mapper ────────────────
        L3_CY = cy + 7
        sx = RX
        stars = float(data.get("star_rating") or 0.0)
        sx, draw = _icon_text_inline(img, draw, sx, L3_CY,
                                     "star", 14, f"{stars:.2f}", self.font_stat_label,
                                     _sr_color(stars))
        sx += 12

        drain = int(data.get("drain_time") or 0)
        dur_text = f"{drain // 60}:{drain % 60:02d}"
        sx, draw = _icon_text_inline(img, draw, sx, L3_CY,
                                     "timer", 14, dur_text, self.font_stat_label,
                                     TEXT_SECONDARY)
        sx += 12

        mapper = data.get("mapper_name") or ""
        if mapper:
            map_t = self._truncate_text(draw, mapper, self.font_stat_label, RP - sx - 4)
            sx, draw = _icon_text_inline(img, draw, sx, L3_CY,
                                         "account", 14, map_t, self.font_stat_label,
                                         TEXT_SECONDARY)

        cy = L3_CY + 10

        # ── Separator — tier-tinted gradient fading to the panel ──────────────
        _draw_gradient_divider(img, RX, cy, RP - RX, 2, tier_color, (50, 50, 66))
        draw = ImageDraw.Draw(img)
        cy += 8

        # ── Conditions (compact Latin, up to 3 lines) ─────────────────────────
        cond_font = self.font_stat_label
        cond_ref = draw.textbbox((0, 0), "Ag", font=cond_font)
        cond_lh = (cond_ref[3] - cond_ref[1]) + 4

        # Prefer conditions_latin (single compact string) + mod badges; fall
        # back to the emoji conditions list only when there's neither.
        cond_latin = data.get("conditions_latin") or ""
        mods_str = data.get("required_mods")
        if cond_latin or _split_mods(mods_str if isinstance(mods_str, str) else None):
            mod_x = RX
            if cond_latin:
                ct = self._truncate_text(draw, cond_latin, cond_font, RW)
                draw.text((RX, cy - cond_ref[1]), ct, font=cond_font, fill=TEXT_SECONDARY)
                cw = draw.textbbox((0, 0), ct, font=cond_font)
                mod_x = RX + (cw[2] - cw[0]) + (10 if ct else 0)
            draw = self._mod_badges_on_line(img, draw, mod_x, cy - cond_ref[1], mods_str, cond_font, size=18)
            cy += cond_lh
        else:
            for line in (data.get("conditions") or [])[:3]:
                lt = self._truncate_text(draw, line, cond_font, RW)
                draw.text((RX, cy - cond_ref[1]), lt, font=cond_font, fill=TEXT_SECONDARY)
                cy += cond_lh

        # ── Footer: [member] count   deadline   [hpssystem] HP ───────────────
        footer_y = THUMB_Y + THUMB_H - 12 - cond_lh
        fx = RX
        p_count = data.get("participant_count", 0)
        max_p = data.get("max_participants")
        p_str = f"{p_count}/{max_p}" if max_p else str(p_count)
        F_CY = footer_y + cond_lh // 2

        fx, draw = _icon_text_inline(img, draw, fx, F_CY,
                                     "member", 14, p_str, cond_font, TEXT_SECONDARY)
        fx += 14

        deadline = data.get("deadline") or "--"
        dl_bb = draw.textbbox((0, 0), deadline, font=cond_font)
        draw.text((fx, footer_y - dl_bb[1]), deadline, font=cond_font, fill=TEXT_SECONDARY)
        fx += (dl_bb[2] - dl_bb[0]) + 14

        hp = data.get("hps_preview_hp")
        if hp:
            hp_icon = load_icon("hpssystem", 14)
            ref_bb = draw.textbbox((0, 0), "Ag", font=cond_font)
            pill_h = (ref_bb[3] - ref_bb[1]) + 2 * 4 + 4
            self._draw_pill(draw, fx, F_CY - pill_h // 2, f"~{hp} HP", ACCENT_GREEN,
                            text_fill=(16, 28, 16), font=cond_font,
                            pad_x=9, pad_y=4, icon=hp_icon, img=img)

        return self._save(img)

    async def generate_bounty_compact_card_async(self, data: Dict) -> BytesIO:
        bsid = data.get("beatmapset_id")
        cover_url = f"https://assets.ppy.sh/beatmaps/{bsid}/covers/cover.jpg" if bsid else None
        cover = None
        if cover_url:
            try:
                r = await download_image(cover_url)
                cover = r if (r and not isinstance(r, Exception)) else None
            except Exception:
                pass
        data = {**data, "beatmap_cover": cover}
        return await asyncio.to_thread(self.generate_bounty_compact_card, data)

    # ─────────────────────────────────────────────────────────────────────────
    # TIER OVERVIEW CARD  (up to 5 bounties per tier, Latin/Torus only)
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bounty_tier_card(self, tier: str, entries: list, offset: int = 0) -> BytesIO:
        """800 × (50+n×82) overview card. Blurred cover bg per row, Torus-only Latin."""
        W = 800
        HEADER_H = 50
        ROW_H = 82
        n = min(len(entries), 5)
        H = HEADER_H + max(n, 1) * ROW_H

        img = Image.new("RGB", (W, H), BG_COLOR)
        tier_color = TIER_COLORS.get(tier, (160, 80, 200))

        # ── Header ────────────────────────────────────────────────────────
        draw = ImageDraw.Draw(img)
        draw.rectangle((0, 0, W, HEADER_H), fill=HEADER_BG)
        draw.rectangle((0, 0, 5, HEADER_H), fill=tier_color)

        tier_label = "OPEN" if tier == "Open" else f"TIER  {tier}"
        self._draw_text_shadow(draw, (18, 7), tier_label, self.font_big, tier_color)

        sr_range = TIER_SR_RANGES.get(tier, "")
        if sr_range:
            star_icon = load_icon("star", size=18)
            sr_bb = draw.textbbox((0, 0), sr_range, font=self.font_subtitle)
            sr_w = sr_bb[2] - sr_bb[0]
            sr_y = (HEADER_H - (sr_bb[3] - sr_bb[1])) // 2
            gap = 5
            star_w = (star_icon.width + gap) if star_icon else 0
            sx = W - 16 - sr_w - star_w
            if star_icon:
                img.paste(star_icon, (sx, (HEADER_H - star_icon.height) // 2), star_icon)
                draw = ImageDraw.Draw(img)
                sx += star_icon.width + gap
            draw.text((sx, sr_y), sr_range, font=self.font_subtitle, fill=tier_color)

        draw.line((0, HEADER_H - 1, W, HEADER_H - 1), fill=(40, 40, 55))

        if not entries:
            draw.text((30, HEADER_H + 26), "NO ACTIVE BOUNTIES IN THIS TIER",
                      font=self.font_subtitle, fill=TEXT_SECONDARY)
            return self._save(img)

        # ── Rows ──────────────────────────────────────────────────────────
        for i, entry in enumerate(entries[:5]):
            y0 = HEADER_H + i * ROW_H
            cover = entry.get("beatmap_cover")
            btype = entry.get("bounty_type") or "First FC"

            # Row background: blurred beatmap cover + type-tinted dark overlay
            if cover:
                try:
                    row_bg = cover_center_crop(cover.convert("RGBA"), W, ROW_H)
                    row_bg = row_bg.filter(ImageFilter.GaussianBlur(10))
                    tint = _type_tint(btype)
                    row_ov = Image.new("RGBA", (W, ROW_H), tint)
                    row_bg = Image.alpha_composite(row_bg, row_ov)
                    img.paste(row_bg.convert("RGB"), (0, y0))
                except Exception:
                    draw.rectangle((0, y0, W, y0 + ROW_H), fill=BG_COLOR)
            else:
                tint_rgb = _type_tint(btype)[:3]
                draw.rectangle((0, y0, W, y0 + ROW_H), fill=tint_rgb)

            # Refresh draw after pasting
            draw = ImageDraw.Draw(img)

            # Left accent per row — type colour
            draw.rectangle((0, y0 + 6, 4, y0 + ROW_H - 6), fill=_type_color(btype))

            # Cover thumbnail — slightly rounded rectangle
            TS_W, TS_H = 82, 62
            tx, ty = 12, y0 + 10
            if cover:
                try:
                    thumb = cover_center_crop(cover.convert("RGBA"), TS_W, TS_H)
                    ov2 = Image.new("RGBA", (TS_W, TS_H), (0, 0, 0, 60))
                    thumb = Image.alpha_composite(thumb, ov2)
                    mask = Image.new("L", (TS_W, TS_H), 0)
                    ImageDraw.Draw(mask).rounded_rectangle(
                        (0, 0, TS_W - 1, TS_H - 1), radius=4, fill=255,
                    )
                    img.paste(thumb.convert("RGB"), (tx, ty), mask)
                    draw = ImageDraw.Draw(img)
                except Exception:
                    draw.rounded_rectangle(
                        (tx, ty, tx + TS_W, ty + TS_H), radius=4, fill=PANEL_BG,
                    )
            else:
                draw.rounded_rectangle(
                    (tx, ty, tx + TS_W, ty + TS_H), radius=4, fill=PANEL_BG,
                )

            RX = tx + TS_W + 10   # 104
            RP = W - 14            # 786

            # Line 1: type pill + [star icon] SR     right: [member icon] count
            L1_CY = y0 + 10 + 11   # vertical center of line 1

            # type pill
            btype_label = _type_pill_label(btype)
            next_bx = self._draw_pill(
                draw, RX, L1_CY - 11, btype_label, _type_color(btype),
                text_fill=(20, 20, 28), font=self.font_stat_label, pad_x=8, pad_y=3,
                img=img,
            )
            next_bx += 10

            # star icon + SR (white, icon 2px higher)
            stars = float(entry.get("star_rating") or 0.0)
            next_bx, draw = _icon_text_inline(
                img, draw, next_bx, L1_CY - 2,
                "star", 13, f"{stars:.2f}", self.font_stat_label, TEXT_PRIMARY,
            )

            # right side: member icon + count, right-aligned (white)
            p_count = entry.get("participant_count", 0)
            max_p = entry.get("max_participants")
            p_str = f"{p_count}/{max_p}" if max_p else str(p_count)

            p_bb = draw.textbbox((0, 0), p_str, font=self.font_stat_label)
            member_icon = load_icon("member", 13)
            member_w = ((member_icon.width + 4) if member_icon else 0) + (p_bb[2] - p_bb[0])
            _, draw = _icon_text_inline(
                img, draw, RP - member_w, L1_CY,
                "member", 13, p_str, self.font_stat_label, TEXT_PRIMARY,
            )

            # Line 2: song title (no difficulty), 3px lower than before
            L2Y = y0 + 37
            beatmap_title = _strip_difficulty(
                entry.get("beatmap_title") or entry.get("title") or "—"
            )
            bt = self._truncate_text(draw, beatmap_title, self.font_label, RP - RX - 20)
            bt_ref = draw.textbbox((0, 0), "Ag", font=self.font_label)
            self._draw_text_shadow(draw, (RX, L2Y - bt_ref[1]), bt,
                                   self.font_label, TEXT_PRIMARY)

            # Line 3: conditions (Latin only) + required-mod badges
            cond_top = y0 + 57
            cond_x = RX
            conditions_raw = entry.get("conditions_latin") or ""
            if conditions_raw:
                circ_r_est = 16
                max_cond_w = RP - circ_r_est * 2 - 20 - RX
                cond_str = self._truncate_text(draw, conditions_raw, self.font_small, max_cond_w)
                draw.text((cond_x, cond_top), cond_str, font=self.font_small, fill=TEXT_SECONDARY)
                cw = draw.textbbox((0, 0), cond_str, font=self.font_small)
                cond_x += (cw[2] - cw[0]) + (10 if cond_str else 0)
            draw = self._mod_badges_on_line(
                img, draw, cond_x, cond_top, entry.get("required_mods"),
                self.font_small, size=18,
            )

            # Slot number — dark circle, bottom-right of row
            slot = str(offset + i + 1)
            slot_bb = draw.textbbox((0, 0), slot, font=self.font_stat_label)
            slot_w_px = slot_bb[2] - slot_bb[0]
            slot_h_px = slot_bb[3] - slot_bb[1]
            circ_r = max(slot_w_px, slot_h_px) // 2 + 6
            circ_cx = RP - circ_r - 8
            circ_cy = y0 + ROW_H - circ_r - 8
            slot_box = (circ_cx - circ_r, circ_cy - circ_r,
                        circ_cx + circ_r, circ_cy + circ_r)
            self._aa_ellipse_fill(img, slot_box, fill=(20, 20, 35))
            self._aa_ellipse_outline(img, slot_box, outline=tier_color, width=2)
            draw = ImageDraw.Draw(img)
            text_x = circ_cx - slot_w_px // 2 - slot_bb[0]
            text_y = circ_cy - slot_h_px // 2 - slot_bb[1]
            draw.text((text_x, text_y), slot, font=self.font_stat_label, fill=tier_color)

        # Gradient dividers between rows
        DIVIDER_H = 3
        for i in range(n - 1):
            c1 = _type_color(entries[i].get("bounty_type"))
            c2 = _type_color(entries[i + 1].get("bounty_type"))
            y_div = HEADER_H + (i + 1) * ROW_H - 1
            _draw_gradient_divider(img, 0, y_div, W, DIVIDER_H, c1, c2)

        return self._save(img)

    async def generate_bounty_tier_card_async(self, tier: str, entries: list,
                                               offset: int = 0) -> BytesIO:
        """Fetch covers for the first 5 entries in parallel, then render."""
        async def _fetch(bsid):
            if not bsid:
                return None
            url = f"https://assets.ppy.sh/beatmaps/{bsid}/covers/cover.jpg"
            try:
                r = await download_image(url)
                return r if (r and not isinstance(r, Exception)) else None
            except Exception:
                return None

        slice5 = entries[:5]
        covers = await asyncio.gather(*[_fetch(e.get("beatmapset_id")) for e in slice5])
        enriched = [{**e, "beatmap_cover": covers[i]} for i, e in enumerate(slice5)]
        return await asyncio.to_thread(self.generate_bounty_tier_card, tier, enriched, offset)

    # ─────────────────────────────────────────────────────────────────────────
    # Drawing helpers (mixin-local)
    # ─────────────────────────────────────────────────────────────────────────

    def _draw_pill(
        self, draw: ImageDraw.Draw, x: int, y: int,
        text: str, bg, *,
        text_fill=(255, 255, 255),
        font=None, pad_x: int = 12, pad_y: int = 4,
        icon: Optional[Image.Image] = None, icon_gap: int = 5,
        img: Optional[Image.Image] = None,
    ) -> int:
        f = font or self.font_stat_label
        bb = draw.textbbox((0, 0), text, font=f)
        # Reference glyph for stable pill height — independent of the text
        # the caller passes in, so neighbouring pills line up.
        ref_bb = draw.textbbox((0, 0), "Ag", font=f)
        ref_h = ref_bb[3] - ref_bb[1]
        tw = bb[2] - bb[0]
        th = bb[3] - bb[1]
        icon_w = icon.width if icon is not None else 0
        extra = (icon_w + icon_gap) if icon is not None else 0
        w = tw + pad_x * 2 + extra
        h = ref_h + pad_y * 2 + 4
        if img is not None:
            self._aa_rounded_fill(img, (x, y, x + w, y + h), radius=h // 2, fill=bg)
        else:
            draw.rounded_rectangle((x, y, x + w, y + h), radius=h // 2, fill=bg)
        cx = x + pad_x
        cy = y + h // 2
        if icon is not None and img is not None:
            iy = cy - icon.height // 2
            img.paste(icon, (cx, iy), icon)
            cx += icon_w + icon_gap
        ty = cy - th // 2 - bb[1]
        draw.text((cx, ty), text, font=f, fill=text_fill)
        return x + w


    def _mod_badges_on_line(
        self, img: Image.Image, draw: ImageDraw.Draw,
        x: int, line_top: int, mods_str, font, *, size: int = 18, spacing: int = 4,
    ) -> ImageDraw.Draw:
        """Draw required-mod badges starting at `x`, vertically centred on a text
        line whose glyph-top sits at `line_top` for `font`. Accepts the raw
        `required_mods` string ("HD,HR" or "HDHR"); a no-op when empty.
        """
        mods = _split_mods(mods_str if isinstance(mods_str, str) else None)
        if not mods:
            return draw
        ref = draw.textbbox((0, 0), "Ag", font=font)
        badge_y = line_top + (ref[1] + ref[3]) // 2 - size // 2
        return self._draw_mod_badges(img, draw, x, badge_y, mods, size=size, spacing=spacing)

    @staticmethod
    def _truncate_text(draw: ImageDraw.Draw, text: str, font, max_w: int) -> str:
        text = text or ""
        bb = draw.textbbox((0, 0), text, font=font)
        if (bb[2] - bb[0]) <= max_w:
            return text
        ell = "…"
        while text and (draw.textbbox((0, 0), text + ell, font=font)[2] -
                        draw.textbbox((0, 0), text + ell, font=font)[0]) > max_w:
            text = text[:-1]
        return (text + ell) if text else ell
