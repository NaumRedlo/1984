"""Bounty card renderers — detail card + list card.

Visual language mirrors the BSK duel cards: blurred beatmap cover as outer
background, sharp cover inside a rounded hero panel, type/status badges,
typographic section headers.
"""

import asyncio
from io import BytesIO
from typing import Dict, List, Optional

from PIL import Image, ImageDraw, ImageFilter

from services.image.constants import (
    BG_COLOR,
    CARD_WIDTH,
    HEADER_BG,
    PADDING_X,
    PANEL_BG,
    ROW_EVEN,
    ROW_ODD,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    ACCENT_RED,
    ACCENT_GREEN,
)
from services.image.utils import (
    cover_center_crop,
    download_image,
    load_icon,
    load_mod_icon,
)
from utils.hp_calculator import BOUNTY_TYPE_MULTIPLIER
from utils.logger import get_logger

logger = get_logger("image.render.bounty")

# Star-rating ranges per tier — shown in tier card header.
TIER_SR_RANGES = {
    "C":    "2.0 – 4.5★",
    "B":    "4.5 – 7.0★",
    "A":    "7.0 – 10.0★",
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


STATUS_COLORS = {
    "active":   (80, 200, 80),
    "expired":  (200, 80, 80),
    "closed":   (140, 140, 160),
    "completed":(80, 140, 220),
}

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


def _paste_icon(img: Image.Image, icon: Optional[Image.Image], x: int, y: int) -> ImageDraw.Draw:
    if icon:
        img.paste(icon, (x, y), icon)
    return ImageDraw.Draw(img)


def _draw_text_stroke(
    draw: ImageDraw.Draw, pos: tuple, text: str, font,
    fill, stroke_fill=(0, 0, 0), stroke_width: int = 1,
):
    x, y = pos
    # Soft drop-shadow: single offset at reduced opacity
    shadow = (*stroke_fill[:3], 120) if len(stroke_fill) == 3 else stroke_fill
    draw.text((x + 1, y + 1), text, font=font, fill=shadow)
    draw.text(pos, text, font=font, fill=fill)


def _rounded_avatar(
    src: Image.Image, size: int,
    *, radius: int = 5, border: int = 2, border_fill=ACCENT_RED,
) -> Image.Image:
    """Rounded-square avatar with a solid border. Output is size×size."""
    src = src.convert("RGBA").resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=255,
    )
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(src, (0, 0), mask)
    if border > 0:
        d = ImageDraw.Draw(out)
        for i in range(border):
            d.rounded_rectangle(
                (i, i, size - 1 - i, size - 1 - i),
                radius=max(radius - i, 1),
                outline=border_fill,
                width=1,
            )
    return out


def _sr_color(stars: float):
    if stars < 2.5:   return (100, 200, 100)
    if stars < 4.0:   return (240, 220, 60)
    if stars < 5.5:   return (255, 140, 50)
    if stars < 7.0:   return (220, 60, 60)
    return (200, 80, 220)


def _type_color(bounty_type: Optional[str]):
    if not bounty_type:
        return DEFAULT_TYPE_COLOR
    return BOUNTY_TYPE_COLORS.get(bounty_type.strip().lower(), DEFAULT_TYPE_COLOR)


def _status_color(status: Optional[str]):
    return STATUS_COLORS.get((status or "").lower(), TEXT_SECONDARY)


def _paint_cover_panel(
    img: Image.Image,
    cover: Optional[Image.Image],
    *,
    x: int, y: int, w: int, h: int,
    inner: tuple,
    radius: int = 16,
    blur_radius: int = 16,
    outer_tint=(8, 8, 14),
    inner_tint=(8, 8, 14),
    outer_alpha: int = 210,
    inner_alpha: int = 130,
    fallback_outer=HEADER_BG,
    fallback_inner=PANEL_BG,
) -> ImageDraw.Draw:
    """Paint outer blurred cover band + sharp inner rounded panel.

    Mirrors `_paint_player_bg` from bsk_duel but parameterised for a single
    landscape hero strip.
    """
    ix1, iy1, ix2, iy2 = inner
    if cover is None:
        img.paste(Image.new("RGB", (w, h), fallback_outer), (x, y))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle(inner, radius=radius, fill=fallback_inner)
        return d
    try:
        rgba = cover.convert("RGBA")
        cropped = cover_center_crop(rgba, w, h)

        blurred = cropped.filter(ImageFilter.GaussianBlur(radius=blur_radius))
        outer_overlay = Image.new("RGBA", (w, h), (*outer_tint, outer_alpha))
        outer = Image.alpha_composite(blurred, outer_overlay)
        img.paste(outer.convert("RGB"), (x, y))

        iw = max(0, ix2 - ix1)
        ih = max(0, iy2 - iy1)
        if iw > 0 and ih > 0:
            inner_crop = cropped.crop((ix1 - x, iy1 - y, ix2 - x, iy2 - y))
            inner_overlay = Image.new("RGBA", (iw, ih), (*inner_tint, inner_alpha))
            inner_blend = Image.alpha_composite(inner_crop, inner_overlay)

            mask = Image.new("L", (iw, ih), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, iw, ih), radius=radius, fill=255,
            )
            img.paste(inner_blend.convert("RGB"), (ix1, iy1), mask)
        return ImageDraw.Draw(img)
    except Exception:
        logger.debug("bounty: cover paint failed, falling back", exc_info=True)
        img.paste(Image.new("RGB", (w, h), fallback_outer), (x, y))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle(inner, radius=radius, fill=fallback_inner)
        return d


class BountyCardMixin:

    # ─────────────────────────────────────────────────────────────────────────
    # DETAIL CARD
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bounty_card(self, data: Dict) -> BytesIO:
        W = CARD_WIDTH
        conditions = data.get("conditions") or []
        mods_list = _split_mods(data.get("required_mods"))

        header_h = 28
        hero_h = 224
        # rows in REQUIREMENTS: conditions + (mods row if any)
        rows = max(len(conditions) + (1 if mods_list else 0), 1)
        row_pitch = 28
        cond_h = 50 + rows * row_pitch + 14
        report_h = 50 + 2 * row_pitch + 14
        if data.get("hps_preview_hp") is not None:
            report_h += row_pitch
        margin = 14
        H = header_h + hero_h + margin + cond_h + margin + report_h + 16

        img, draw = self._create_canvas(W, H)

        bounty_id = data.get("bounty_id", "?")
        self._draw_header(draw, "PROJECT 1984 — BOUNTY DIRECTIVE", "", W)

        # ── HERO (cover) ─────────────────────────────────────────────────────
        hero_x, hero_y = 0, header_h
        inner_pad = 12
        inner_box = (
            hero_x + inner_pad, hero_y + inner_pad,
            hero_x + W - inner_pad, hero_y + hero_h - inner_pad,
        )
        cover = data.get("beatmap_cover")
        draw = _paint_cover_panel(
            img, cover,
            x=hero_x, y=hero_y, w=W, h=hero_h,
            inner=inner_box,
            outer_tint=(6, 6, 12), outer_alpha=215,
            inner_tint=(8, 8, 16), inner_alpha=135,
        )

        # Badges row inside hero
        bx = inner_box[0] + 16
        by = inner_box[1] + 14
        bx = self._draw_pill(
            draw, bx, by,
            (data.get("bounty_type") or "BOUNTY").upper(),
            _type_color(data.get("bounty_type")),
            text_fill=(20, 20, 28),
        )
        bx += 8
        status = (data.get("status") or "active").lower()
        sc = _status_color(status)
        bx = self._draw_pill(
            draw, bx, by,
            status.upper(), sc, text_fill=(20, 20, 28),
        )

        # Host avatar + nickname — right side of badges row
        host_name = data.get("host_name") or ""
        host_avatar = data.get("host_avatar")
        if host_name or host_avatar is not None:
            host_av_size = 36
            right_edge = inner_box[2] - 16
            hax = right_edge - host_av_size
            ref_bb = draw.textbbox((0, 0), "Ag", font=self.font_stat_label)
            pill_h = (ref_bb[3] - ref_bb[1]) + 2 * 4 + 4
            hav_y = by + (pill_h - host_av_size) // 2
            if host_avatar is not None:
                try:
                    av = _rounded_avatar(host_avatar, host_av_size, radius=8, border=1)
                    draw = _paste_icon(img, av, hax, hav_y)
                except Exception:
                    logger.debug("bounty detail: host avatar paste failed", exc_info=True)
            # "hosted by" + nickname vertically centred as a block against the avatar
            label_bb = draw.textbbox((0, 0), "hosted by", font=self.font_stat_label)
            label_w = label_bb[2] - label_bb[0]
            label_h = label_bb[3] - label_bb[1]
            hn_bb = draw.textbbox((0, 0), host_name or "", font=self.font_stat_label)
            hn_w = hn_bb[2] - hn_bb[0]
            hn_h = hn_bb[3] - hn_bb[1]
            gap = 5
            block_h = label_h + gap + hn_h
            block_y = hav_y + (host_av_size - block_h) // 2
            label_x = hax - 8 - label_w
            label_y = block_y - label_bb[1]
            _draw_text_stroke(draw, (label_x, label_y), "hosted by", self.font_stat_label, fill=TEXT_SECONDARY)
            if host_name:
                hn_x = hax - 8 - hn_w
                hn_y = block_y + label_h + gap - hn_bb[1]
                _draw_text_stroke(
                    draw, (hn_x, hn_y),
                    host_name, self.font_stat_label, fill=TEXT_PRIMARY,
                )

        # Title
        title = data.get("title", "—") or "—"
        title_truncated = self._truncate_text(draw, title, self.font_big, W - 2 * inner_pad - 40)
        _draw_text_stroke(
            draw, (inner_box[0] + 16, by + 38),
            title_truncated, self.font_big, fill=TEXT_PRIMARY,
        )

        # Beatmap title
        beatmap_title = data.get("beatmap_title", "") or ""
        if beatmap_title:
            bt_truncated = self._truncate_text(draw, beatmap_title, self.font_subtitle, W - 2 * inner_pad - 40)
            _draw_text_stroke(
                draw, (inner_box[0] + 16, by + 82),
                bt_truncated, self.font_subtitle, fill=TEXT_SECONDARY,
            )

        # Mapper row (avatar + name) just under beatmap title
        mapper_name = data.get("mapper_name") or ""
        mapper_avatar = data.get("mapper_avatar")
        mapper_y = by + 109
        avatar_size = 36
        if mapper_name or mapper_avatar:
            ax = inner_box[0] + 16
            if mapper_avatar is not None:
                try:
                    av = _rounded_avatar(mapper_avatar, avatar_size)
                    draw = _paste_icon(img, av, ax, mapper_y)
                except Exception:
                    logger.debug("bounty: mapper avatar paste failed", exc_info=True)
            tx = ax + avatar_size + 10
            # "mapped by" label
            mb_bb = draw.textbbox((0, 0), "mapped by", font=self.font_stat_label)
            mb_h = mb_bb[3] - mb_bb[1]
            mb_y = mapper_y - mb_bb[1]
            _draw_text_stroke(draw, (tx, mb_y), "mapped by", self.font_stat_label, fill=TEXT_SECONDARY)
            if mapper_name:
                name_bb = draw.textbbox((0, 0), mapper_name, font=self.font_label)
                name_y = mb_y + mb_h + 2 - name_bb[1]
                _draw_text_stroke(
                    draw, (tx, name_y),
                    mapper_name, self.font_label, fill=TEXT_PRIMARY,
                )

        # Stats row inside hero bottom — icons + SR text, all vertically centred.
        # SR is rendered as plain icon + text to match the duration display style.
        row_h = 22
        sy_top = inner_box[3] - 16 - row_h + 3
        sx = inner_box[0] + 16
        stars = float(data.get("star_rating") or 0.0)

        sr_text = f"{stars:.2f}"
        star_icon = load_icon("star", size=18)
        sr_bb = draw.textbbox((0, 0), sr_text, font=self.font_row)
        sr_text_h = sr_bb[3] - sr_bb[1]
        sr_text_y = sy_top + (row_h - sr_text_h) // 2 - sr_bb[1]
        if star_icon:
            icon_y = sy_top + (row_h - star_icon.height) // 2
            draw = _paste_icon(img, star_icon, sx, icon_y)
            sx += star_icon.width + 3
        _draw_text_stroke(draw, (sx, sr_text_y), sr_text, self.font_row, fill=TEXT_PRIMARY)
        sx += (sr_bb[2] - sr_bb[0]) + 10

        duration = data.get("duration")
        if duration is not None:
            try:
                d_int = int(duration)
                dur_text = f"{d_int // 60}:{d_int % 60:02d}"
            except Exception:
                dur_text = "—"
            timer_icon = load_icon("timer", size=18)
            dur_bb = draw.textbbox((0, 0), dur_text, font=self.font_row)
            text_h = dur_bb[3] - dur_bb[1]
            text_y = sy_top + (row_h - text_h) // 2 - dur_bb[1]
            if timer_icon:
                icon_y = sy_top + (row_h - timer_icon.height) // 2
                draw = _paste_icon(img, timer_icon, sx, icon_y)
                sx += timer_icon.width + 3
            _draw_text_stroke(draw, (sx, text_y), dur_text, self.font_row, fill=TEXT_PRIMARY)
            sx += (dur_bb[2] - dur_bb[0]) + 10

        # Bounty ID right-aligned in the stats row
        bid_text = f"#{bounty_id}"
        bid_bb = draw.textbbox((0, 0), bid_text, font=self.font_row)
        bid_h = bid_bb[3] - bid_bb[1]
        bid_y = sy_top + (row_h - bid_h) // 2 - bid_bb[1]
        _draw_text_stroke(
            draw, (inner_box[2] - 16 - (bid_bb[2] - bid_bb[0]), bid_y),
            bid_text, self.font_row, fill=ACCENT_RED,
        )

        # ── REQUIREMENTS panel ──────────────────────────────────────────────
        py = header_h + hero_h + margin
        self._draw_section_panel(
            draw,
            x=PADDING_X, y=py, w=W - 2 * PADDING_X, h=cond_h,
            title="REQUIREMENTS",
        )
        cy = py + 50
        if conditions or mods_list:
            for cond in conditions:
                self._draw_bullet(draw, PADDING_X + 16, cy, cond)
                cy += row_pitch
            if mods_list:
                self._draw_mods_row(img, draw, PADDING_X + 16, cy, mods_list)
                cy += row_pitch
        else:
            self._draw_bullet(draw, PADDING_X + 16, cy, "No special conditions", fill=TEXT_SECONDARY)

        # ── FIELD REPORT panel ──────────────────────────────────────────────
        ry = py + cond_h + margin
        self._draw_section_panel(
            draw,
            x=PADDING_X, y=ry, w=W - 2 * PADDING_X, h=report_h,
            title="FIELD REPORT",
        )
        rly = ry + 50

        p_count = data.get("participant_count", 0)
        max_p = data.get("max_participants")
        p_str = f"{p_count}/{max_p}" if max_p else str(p_count)
        self._draw_field_row(draw, PADDING_X + 16, rly, "Participants:", p_str)
        rly += row_pitch

        deadline = data.get("deadline", "—") or "—"
        self._draw_field_row(draw, PADDING_X + 16, rly, "Deadline:", str(deadline))
        rly += row_pitch

        hps_preview = data.get("hps_preview_hp")
        if hps_preview is not None:
            self._draw_field_row(
                draw, PADDING_X + 16, rly,
                "HPS Preview (Win):", f"~{hps_preview} HP",
                value_fill=ACCENT_GREEN,
            )

        return self._save(img)

    async def generate_bounty_card_async(self, data: Dict) -> BytesIO:
        bsid = data.get("beatmapset_id")
        avatar_url = data.get("mapper_avatar_url")
        host_avatar_url = data.get("host_avatar_url")

        async def _fetch(url: Optional[str]) -> Optional[Image.Image]:
            if not url:
                return None
            try:
                r = await download_image(url)
                return r if (r and not isinstance(r, Exception)) else None
            except Exception:
                return None

        cover_url = f"https://assets.ppy.sh/beatmaps/{bsid}/covers/cover.jpg" if bsid else None
        cover, mapper_avatar, host_avatar = await asyncio.gather(
            _fetch(cover_url), _fetch(avatar_url), _fetch(host_avatar_url),
        )
        data = {**data, "beatmap_cover": cover, "mapper_avatar": mapper_avatar, "host_avatar": host_avatar}
        return await asyncio.to_thread(self.generate_bounty_card, data)

    # ─────────────────────────────────────────────────────────────────────────
    # LIST CARD
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bountylist_card(self, entries: List[Dict]) -> BytesIO:
        W = CARD_WIDTH
        header_h = 28
        row_h = 84
        row_gap = 10
        top_pad = 14
        bottom_pad = 16

        num_rows = max(len(entries), 1)
        H = header_h + top_pad + num_rows * row_h + (num_rows - 1) * row_gap + bottom_pad

        img, draw = self._create_canvas(W, H)
        count_str = f"{len(entries)}" if entries else "0"
        self._draw_header(draw, "PROJECT 1984 — ACTIVE BOUNTIES", count_str, W)

        if not entries:
            ey = header_h + top_pad + (row_h - 24) // 2
            self._draw_panel(draw, PADDING_X, header_h + top_pad, W - 2 * PADDING_X, row_h)
            draw.text((PADDING_X + 20, ey), "No active bounties", font=self.font_row, fill=TEXT_SECONDARY)
            return self._save(img)

        for i, entry in enumerate(entries):
            y_top = header_h + top_pad + i * (row_h + row_gap)
            cover = entry.get("beatmap_cover")
            inner_box = (
                PADDING_X, y_top,
                W - PADDING_X, y_top + row_h,
            )
            _paint_cover_panel(
                img, cover,
                x=0, y=y_top, w=W, h=row_h,
                inner=inner_box,
                radius=12, blur_radius=10,
                outer_tint=BG_COLOR, outer_alpha=255,  # outer band stays flat (BG)
                inner_tint=(8, 8, 16), inner_alpha=170,
                fallback_outer=BG_COLOR,
                fallback_inner=PANEL_BG,
            )

            d = ImageDraw.Draw(img)

            # Type badge top-left
            badge_x = inner_box[0] + 14
            badge_y = inner_box[1] + 12
            type_color = _type_color(entry.get("bounty_type"))
            type_label = (entry.get("bounty_type") or "BOUNTY").upper()
            self._draw_pill(
                d, badge_x, badge_y,
                type_label, type_color,
                text_fill=(20, 20, 28),
                font=self.font_stat_label,
                pad_x=10, pad_y=3,
            )

            # Bounty ID right-aligned, vertically centred to the type pill
            pill_bb = d.textbbox((0, 0), "Ag", font=self.font_stat_label)
            pill_h = (pill_bb[3] - pill_bb[1]) + 2 * 3 + 4
            pill_cy = badge_y + pill_h // 2
            bid = f"#{entry.get('bounty_id', '?')}"
            bb = d.textbbox((0, 0), bid, font=self.font_label)
            bid_h = bb[3] - bb[1]
            bid_y = pill_cy - bid_h // 2 - bb[1]
            _draw_text_stroke(
                d, (inner_box[2] - 14 - (bb[2] - bb[0]), bid_y),
                bid, self.font_label, fill=ACCENT_RED,
            )

            # Host row under the bounty ID: avatar on the right, nickname to
            # its left. Both right-aligned so they tuck under the ID.
            host_name = entry.get("host_name") or ""
            host_avatar = entry.get("host_avatar")
            if host_name or host_avatar is not None:
                host_av_size = 28
                host_row_y = bid_y + bid_h + 10
                right_edge = inner_box[2] - 14
                ax = right_edge - host_av_size
                if host_avatar is not None:
                    try:
                        av = _rounded_avatar(host_avatar, host_av_size, radius=6, border=1)
                        _paste_icon(img, av, ax, host_row_y)
                    except Exception:
                        logger.debug("bounty list: host avatar paste failed", exc_info=True)
                if host_name:
                    name_bb = d.textbbox((0, 0), host_name, font=self.font_stat_label)
                    name_w = name_bb[2] - name_bb[0]
                    name_h = name_bb[3] - name_bb[1]
                    ny = host_row_y + (host_av_size - name_h) // 2 - name_bb[1]
                    _draw_text_stroke(
                        d, (ax - 8 - name_w, ny),
                        host_name, self.font_stat_label, fill=TEXT_SECONDARY,
                    )

            # Title
            title = entry.get("title", "—") or "—"
            title = self._truncate_text(d, title, self.font_row, inner_box[2] - inner_box[0] - 32)
            _draw_text_stroke(
                d, (inner_box[0] + 14, badge_y + 22),
                title, self.font_row, fill=TEXT_PRIMARY,
            )

            # Sub-line: deadline · participants (icons from assets)
            stars = float(entry.get("star_rating") or 0.0)
            deadline = entry.get("deadline", "—") or "—"
            p_count = entry.get("participant_count", 0)
            max_p = entry.get("max_participants")
            p_str = f"{p_count}/{max_p}" if max_p else str(p_count)

            type_bb = d.textbbox((0, 0), "Ag", font=self.font_stat_label)
            pill_h_list = (type_bb[3] - type_bb[1]) + 2 * 3 + 4
            sub_row_h = pill_h_list
            sy_top = badge_y + 46
            cy = sy_top + sub_row_h // 2
            sx = inner_box[0] + 14

            # SR as plain icon + text (no pill)
            sr_text = f"{stars:.2f}"
            star_icon_s = load_icon("star", size=12)
            sr_bb = d.textbbox((0, 0), sr_text, font=self.font_stat_label)
            sr_th = sr_bb[3] - sr_bb[1]
            sr_ty = cy - sr_th // 2 - sr_bb[1]
            if star_icon_s:
                _paste_icon(img, star_icon_s, sx, cy - star_icon_s.height // 2)
                sx += star_icon_s.width + 3
            _draw_text_stroke(d, (sx, sr_ty), sr_text, self.font_stat_label, fill=TEXT_PRIMARY)
            sx += (sr_bb[2] - sr_bb[0]) + 8

            def _center_paste(icon, ix):
                _paste_icon(img, icon, ix, cy - icon.height // 2)
                return ix + icon.width + 3

            def _center_text(text, tx, fill):
                bb = d.textbbox((0, 0), text, font=self.font_label)
                th = bb[3] - bb[1]
                ty = cy - th // 2 - bb[1]
                _draw_text_stroke(d, (tx, ty), text, self.font_label, fill=fill)
                return tx + (bb[2] - bb[0])

            timer_icon_s = load_icon("timer", size=14)
            if timer_icon_s:
                sx = _center_paste(timer_icon_s, sx)
            sx = _center_text(str(deadline), sx, TEXT_SECONDARY) + 10

            member_icon = load_icon("member", size=14)
            if member_icon:
                sx = _center_paste(member_icon, sx)
            _center_text(p_str, sx, TEXT_SECONDARY)

        return self._save(img)

    async def generate_bountylist_card_async(self, entries: List[Dict]) -> BytesIO:
        async def _fetch_url(url: Optional[str]) -> Optional[Image.Image]:
            if not url:
                return None
            try:
                r = await download_image(url)
                return r if (r and not isinstance(r, Exception)) else None
            except Exception:
                return None

        async def _fetch_cover(entry):
            bsid = entry.get("beatmapset_id")
            if not bsid:
                return None
            return await _fetch_url(
                f"https://assets.ppy.sh/beatmaps/{bsid}/covers/cover.jpg"
            )

        cover_tasks = [_fetch_cover(e) for e in entries]
        host_tasks = [_fetch_url(e.get("host_avatar_url")) for e in entries]
        covers, hosts = await asyncio.gather(
            asyncio.gather(*cover_tasks), asyncio.gather(*host_tasks),
        )
        enriched = [
            {**e, "beatmap_cover": covers[i], "host_avatar": hosts[i]}
            for i, e in enumerate(entries)
        ]
        return await asyncio.to_thread(self.generate_bountylist_card, enriched)

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
                             font=self.font_stat_label, pad_x=9, pad_y=4)
        bx += 6
        bx = self._draw_pill(draw, bx, cy, _type_pill_label(btype), _type_color(btype),
                             text_fill=(20, 20, 28), font=self.font_stat_label,
                             pad_x=9, pad_y=4)

        bid_text = f"#{data.get('bounty_id', '?')}"
        bid_bb = draw.textbbox((0, 0), bid_text, font=self.font_stat_label)
        draw.text((RP - (bid_bb[2] - bid_bb[0]), cy - bid_bb[1]),
                  bid_text, font=self.font_stat_label, fill=ACCENT_RED)

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

        # ── Separator ─────────────────────────────────────────────────────────
        draw.line((RX, cy, RP, cy), fill=(70, 70, 90), width=1)
        cy += 8

        # ── Conditions (compact Latin, up to 3 lines) ─────────────────────────
        cond_font = self.font_stat_label
        cond_ref = draw.textbbox((0, 0), "Ag", font=cond_font)
        cond_lh = (cond_ref[3] - cond_ref[1]) + 4

        # Prefer conditions_latin (single compact string), fall back to conditions list
        cond_latin = data.get("conditions_latin") or ""
        if cond_latin:
            ct = self._truncate_text(draw, cond_latin, cond_font, RW)
            draw.text((RX, cy - cond_ref[1]), ct, font=cond_font, fill=TEXT_SECONDARY)
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
            fx, draw = _icon_text_inline(img, draw, fx, F_CY,
                                         "hpssystem", 14, f"~{hp} HP",
                                         cond_font, ACCENT_GREEN)

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
            sr_bb = draw.textbbox((0, 0), sr_range, font=self.font_subtitle)
            sr_y = (HEADER_H - (sr_bb[3] - sr_bb[1])) // 2
            draw.text((W - 16 - (sr_bb[2] - sr_bb[0]), sr_y), sr_range,
                      font=self.font_subtitle, fill=tier_color)

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

            # Line 3: conditions (Latin only, truncated to avoid slot circle)
            conditions_raw = entry.get("conditions_latin") or ""
            if conditions_raw:
                circ_r_est = 16
                max_cond_w = RP - circ_r_est * 2 - 20 - RX
                cond_str = self._truncate_text(draw, conditions_raw, self.font_small, max_cond_w)
                draw.text((RX, y0 + 57), cond_str, font=self.font_small, fill=TEXT_SECONDARY)

            # Slot number — dark circle, bottom-right of row
            slot = str(offset + i + 1)
            slot_bb = draw.textbbox((0, 0), slot, font=self.font_stat_label)
            slot_w_px = slot_bb[2] - slot_bb[0]
            slot_h_px = slot_bb[3] - slot_bb[1]
            circ_r = max(slot_w_px, slot_h_px) // 2 + 6
            circ_cx = RP - circ_r - 8
            circ_cy = y0 + ROW_H - circ_r - 8
            draw.ellipse(
                (circ_cx - circ_r, circ_cy - circ_r, circ_cx + circ_r, circ_cy + circ_r),
                fill=(20, 20, 35),
            )
            text_x = circ_cx - slot_w_px // 2 - slot_bb[0]
            text_y = circ_cy - slot_h_px // 2 - slot_bb[1]
            draw.text((text_x, text_y), slot, font=self.font_stat_label, fill=TEXT_PRIMARY)

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

    def _draw_section_panel(
        self, draw: ImageDraw.Draw,
        *, x: int, y: int, w: int, h: int, title: str,
    ):
        draw.rounded_rectangle((x, y, x + w, y + h), radius=12, fill=PANEL_BG)
        draw.line(
            [(x + 16, y + 36), (x + w - 16, y + 36)],
            fill=ACCENT_RED, width=1,
        )
        draw.text(
            (x + 16, y + 10),
            title, font=self.font_subtitle, fill=ACCENT_RED,
        )

    # Shared row geometry — keeps bullets, labels, badges, and values on the
    # same horizontal centre line across REQUIREMENTS and FIELD REPORT.
    ROW_H = 22
    BULLET_D = 6

    def _row_center(self, y: int) -> int:
        return y + self.ROW_H // 2

    def _text_y_centered(self, draw: ImageDraw.Draw, y: int, text: str, font) -> int:
        bb = draw.textbbox((0, 0), text, font=font)
        th = bb[3] - bb[1]
        return y + (self.ROW_H - th) // 2 - bb[1]

    def _draw_bullet(self, draw: ImageDraw.Draw, x: int, y: int, text: str, fill=TEXT_PRIMARY):
        cy = self._row_center(y)
        r = self.BULLET_D // 2
        draw.ellipse((x, cy - r, x + self.BULLET_D, cy + r), fill=ACCENT_RED)
        ty = self._text_y_centered(draw, y, text, self.font_label)
        draw.text((x + self.BULLET_D + 8, ty), text, font=self.font_label, fill=fill)

    def _draw_mods_row(
        self, img: Image.Image, draw: ImageDraw.Draw,
        x: int, y: int, mods: List[str],
    ):
        cy = self._row_center(y)
        r = self.BULLET_D // 2
        draw.ellipse((x, cy - r, x + self.BULLET_D, cy + r), fill=ACCENT_RED)
        label = "Mods:"
        ty = self._text_y_centered(draw, y, label, self.font_label)
        draw.text((x + self.BULLET_D + 8, ty), label, font=self.font_label, fill=TEXT_PRIMARY)
        bb = draw.textbbox((0, 0), label, font=self.font_label)
        bx = x + self.BULLET_D + 8 + (bb[2] - bb[0]) + 10

        badge_size = 28
        py = cy - badge_size // 2
        self._draw_mod_badges(img, draw, bx, py, mods, size=badge_size, spacing=6)

    def _draw_field_row(
        self, draw: ImageDraw.Draw, x: int, y: int,
        label: str, value: str,
        *, value_fill=TEXT_PRIMARY,
    ):
        ly = self._text_y_centered(draw, y, label, self.font_label)
        draw.text((x, ly), label, font=self.font_label, fill=TEXT_SECONDARY)
        bb = draw.textbbox((0, 0), label, font=self.font_label)
        vy = self._text_y_centered(draw, y, value, self.font_row)
        draw.text(
            (x + (bb[2] - bb[0]) + 8, vy),
            value, font=self.font_row, fill=value_fill,
        )

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
