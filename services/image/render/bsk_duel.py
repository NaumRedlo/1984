"""BSK duel phase card renderers."""

import asyncio
from io import BytesIO
from typing import Dict, Optional

from PIL import Image, ImageDraw

from services.image.constants import (
    HEADER_BG, ROW_EVEN, ROW_ODD,
    TEXT_PRIMARY, TEXT_SECONDARY,
    ACCENT_RED, ACCENT_GREEN,
    PADDING_X, CARD_WIDTH,
)
from services.image.utils import load_icon, load_flag, download_image, cover_center_crop, _none_coro
from utils.logger import get_logger

logger = get_logger("image.render.bsk_duel")

# Player accent colours
P1_COLOR = (210, 70, 70)    # red
P2_COLOR = (70, 120, 210)   # blue
GOLD = (255, 200, 50)

SKILL_COLORS = {
    'aim':   (200, 80,  80),
    'speed': (80,  140, 220),
    'acc':   (80,  200, 120),
    'cons':  (200, 180, 60),
}
SKILL_LABELS = {
    'aim':   'AIM',
    'speed': 'SPEED',
    'acc':   'ACC',
    'cons':  'CONS',
}
SKILL_KEYS = ['aim', 'speed', 'acc', 'cons']

# Map type → cell tint bg (used when no cover available)
MTYPE_BG = {
    'aim':   (44, 22, 22),
    'speed': (20, 28, 50),
    'acc':   (20, 40, 26),
    'cons':  (40, 36, 16),
}

# Full map type labels for badges
MTYPE_FULL = {
    'aim':   'AIM',
    'speed': 'SPEED',
    'acc':   'ACCURACY',
    'cons':  'CONSISTENCY',
}


def _paste_icon(img: Image.Image, icon: Image.Image, x: int, y: int) -> ImageDraw.Draw:
    """Paste RGBA icon onto RGB image, return fresh Draw."""
    if icon:
        img.paste(icon, (x, y), icon)
    return ImageDraw.Draw(img)


def _sr_color(stars: float):
    if stars < 2.5:
        return (100, 200, 100)
    elif stars < 4.0:
        return (240, 220, 60)
    elif stars < 5.5:
        return (255, 140, 50)
    elif stars < 7.0:
        return (220, 60, 60)
    else:
        return (200, 80, 220)


def _draw_name_with_flag(
    img: Image.Image, draw: ImageDraw.Draw,
    x: int, y: int, name: str, country: str,
    font, color, align: str = 'left',
    flag_h: int = 16,
    flag_y_offset: int = 0,
) -> ImageDraw.Draw:
    """
    align='left'  → [flag] name   (x is left edge)
    align='right' → name [flag]   (x is right edge, flag to the RIGHT of name)
    """
    flag = load_flag(country, height=flag_h) if country else None
    flag_w = flag.width if flag else 0
    gap = 6 if flag else 0

    name_bbox = draw.textbbox((0, 0), name, font=font)
    name_w = name_bbox[2] - name_bbox[0]
    name_h = name_bbox[3] - name_bbox[1]
    flag_mid_offset = (name_h - flag_h) // 2

    if align == 'left':
        fx = x
        tx = x + flag_w + gap
    elif align == 'right_ltr':
        # block is [flag] name with RIGHT edge at x  (flag left of name, like pf)
        block_w = flag_w + gap + name_w
        fx = x - block_w
        tx = x - name_w
    else:
        # right: name to the left, flag to the RIGHT — x is the far-right edge
        fx = x - flag_w          # flag starts here (rightmost)
        tx = x - flag_w - gap - name_w  # name ends before flag

    if flag:
        draw = _paste_icon(img, flag, fx, y + max(0, flag_mid_offset) + flag_y_offset)
    draw.text((tx, y), name, font=font, fill=color)
    return ImageDraw.Draw(img)


# ─────────────────────────────────────────────────────────────────────────────

class BskDuelCardMixin:

    # ─────────────────────────────────────────────────────────────────────────
    # PICK CARD  (3×2 grid, updates per pick)
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_pick_card(self, data: Dict) -> BytesIO:
        """
        data keys:
          round_number, p1_name, p2_name, p1_country, p2_country
          p1_cover, p2_cover  PIL.Image|None  — player profile backgrounds
          p1_picked  int|None, p2_picked  int|None
          candidates  list[dict]:
            beatmap_id, beatmapset_id, title, artist, version, star_rating, map_type
          covers  list[PIL.Image|None]  — pre-downloaded, same order as candidates
        """
        W = CARD_WIDTH
        header_h = 36
        status_h = 44          # slightly taller for cover BG
        cell_pad = 8
        grid_cols = 3
        grid_rows = 2
        cell_w = (W - 2 * cell_pad - (grid_cols - 1) * cell_pad) // grid_cols
        cell_h = 140
        grid_h = grid_rows * cell_h + (grid_rows - 1) * cell_pad + cell_pad * 2
        H = header_h + status_h + grid_h

        img, draw = self._create_canvas(W, H)
        round_num = data.get('round_number', 1)
        p1_name = data.get('p1_name', 'P1')
        p2_name = data.get('p2_name', 'P2')
        p1_country = data.get('p1_country', '')
        p2_country = data.get('p2_country', '')
        p1_cover = data.get('p1_cover')
        p2_cover = data.get('p2_cover')
        p1_picked = data.get('p1_picked')
        p2_picked = data.get('p2_picked')
        candidates = data.get('candidates', [])
        covers = data.get('covers', [])

        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL', f'Round {round_num} · Map Pick', W)

        # ── Status bar with player cover backgrounds ──────────────────────────
        y_status = header_h
        half_w = W // 2

        def _draw_player_bg(cover_img, x0, w, tint_rgb):
            if cover_img:
                try:
                    cr = cover_center_crop(cover_img.convert("RGBA"), w, status_h)
                    ov = Image.new("RGBA", (w, status_h), (0, 0, 0, 160))
                    bl = Image.alpha_composite(cr, ov)
                    ti = Image.new("RGBA", (w, status_h), (*tint_rgb, 55))
                    bl = Image.alpha_composite(bl, ti)
                    img.paste(bl.convert("RGB"), (x0, y_status))
                except Exception:
                    draw.rectangle([(x0, y_status), (x0 + w, y_status + status_h)], fill=HEADER_BG)
            else:
                draw.rectangle([(x0, y_status), (x0 + w, y_status + status_h)], fill=HEADER_BG)
            return ImageDraw.Draw(img)

        draw = _draw_player_bg(p1_cover, 0, half_w, P1_COLOR)
        draw = _draw_player_bg(p2_cover, half_w, W - half_w, P2_COLOR)

        p1_ready = p1_picked is not None
        p2_ready = p2_picked is not None
        p1_col = ACCENT_GREEN if p1_ready else (230, 230, 240)
        p2_col = ACCENT_GREEN if p2_ready else (230, 230, 240)

        name_y = y_status + (status_h - 16) // 2  # vertically center 16-px flag

        # P1: [flag] name — left side
        draw = _draw_name_with_flag(
            img, draw, PADDING_X, name_y,
            p1_name, p1_country, self.font_label, p1_col,
            align='left', flag_h=16,
        )
        # P2: name [flag] — right side
        draw = _draw_name_with_flag(
            img, draw, W - PADDING_X, name_y,
            p2_name, p2_country, self.font_label, p2_col,
            align='right', flag_h=16,
        )

        # Center divider
        draw.line([(W // 2, y_status + 6), (W // 2, y_status + status_h - 6)],
                  fill=(80, 80, 100), width=1)

        # ── Map grid ─────────────────────────────────────────────────────────
        y_grid_start = header_h + status_h + cell_pad
        star_icon = load_icon('star', size=12)

        for idx in range(6):
            col = idx % grid_cols
            row = idx // grid_cols
            cx_cell = cell_pad + col * (cell_w + cell_pad)
            cy_cell = y_grid_start + row * (cell_h + cell_pad)

            mtype = candidates[idx].get('map_type', '') if idx < len(candidates) else ''
            cell_bg = MTYPE_BG.get(mtype, (28, 28, 44))

            draw.rounded_rectangle(
                (cx_cell, cy_cell, cx_cell + cell_w, cy_cell + cell_h),
                radius=8, fill=cell_bg,
            )

            # ── Cover image background ────────────────────────────────────────
            cover_img = covers[idx] if idx < len(covers) else None
            if cover_img:
                try:
                    cropped = cover_center_crop(cover_img.convert("RGBA"), cell_w, cell_h)
                    overlay = Image.new("RGBA", (cell_w, cell_h), (0, 0, 0, 170))
                    blended = Image.alpha_composite(cropped, overlay)
                    tint_col = MTYPE_BG.get(mtype, (0, 0, 0))
                    tint = Image.new("RGBA", (cell_w, cell_h), (*tint_col, 80))
                    blended = Image.alpha_composite(blended, tint)
                    img.paste(blended.convert("RGB"), (cx_cell, cy_cell))
                    draw = ImageDraw.Draw(img)
                except Exception:
                    logger.debug("bsk_pick_card: cover composite failed", exc_info=True)

            if idx >= len(candidates):
                self._text_center(draw, cx_cell + cell_w // 2, cy_cell + cell_h // 2 - 8,
                                   '—', self.font_label, (60, 60, 80))
                continue

            m = candidates[idx]
            bid = m.get('beatmap_id')
            title = m.get('title', 'Unknown')
            artist = m.get('artist', '')
            version = m.get('version', '')
            stars = m.get('star_rating', 0.0)
            sr_col = _sr_color(stars)

            # Pick state
            p1_chose = (p1_picked == bid)
            p2_chose = (p2_picked == bid)
            both = p1_chose and p2_chose

            border_col = GOLD if both else (P1_COLOR if p1_chose else (P2_COLOR if p2_chose else (55, 55, 75)))
            border_w = 3 if (p1_chose or p2_chose) else 1
            draw.rounded_rectangle(
                (cx_cell, cy_cell, cx_cell + cell_w, cy_cell + cell_h),
                radius=8, outline=border_col, width=border_w,
            )

            # Map type colour accent strip at top
            type_color = SKILL_COLORS.get(mtype)
            if type_color:
                draw.rounded_rectangle(
                    (cx_cell, cy_cell, cx_cell + cell_w, cy_cell + 4),
                    radius=2, fill=type_color,
                )

            # ── Title ────────────────────────────────────────────────────────
            max_title = 20
            disp_title = title if len(title) <= max_title else title[:max_title - 1] + '…'
            draw.text((cx_cell + 8, cy_cell + 10), disp_title,
                      font=self.font_label, fill=TEXT_PRIMARY)

            # ── Artist ───────────────────────────────────────────────────────
            max_artist = 24
            disp_artist = artist if len(artist) <= max_artist else artist[:max_artist - 1] + '…'
            draw.text((cx_cell + 8, cy_cell + 32), disp_artist,
                      font=self.font_small, fill=TEXT_SECONDARY)

            # ── Version ──────────────────────────────────────────────────────
            max_ver = 26
            disp_ver = f'[{version}]' if version else ''
            if len(disp_ver) > max_ver:
                disp_ver = disp_ver[:max_ver - 1] + '…'
            draw.text((cx_cell + 8, cy_cell + 50), disp_ver,
                      font=self.font_stat_label, fill=(110, 135, 185))

            # ── Map type badge (full name, bottom-left area) ──────────────────
            if type_color:
                type_lbl = MTYPE_FULL.get(mtype, '')
                if type_lbl:
                    lbl_bb = draw.textbbox((0, 0), type_lbl, font=self.font_stat_label)
                    lbl_w = lbl_bb[2] - lbl_bb[0]
                    badge_x = cx_cell + 7
                    badge_y = cy_cell + 72
                    draw.rounded_rectangle(
                        (badge_x, badge_y, badge_x + lbl_w + 10, badge_y + 17),
                        radius=4, fill=type_color,
                    )
                    draw.text((badge_x + 5, badge_y + 1), type_lbl,
                              font=self.font_stat_label, fill=(18, 18, 28))

            # ── Pick indicator stripe (bottom) ────────────────────────────────
            stripe_y = None
            if p1_chose or p2_chose:
                stripe_y = cy_cell + cell_h - 26
                stripe_col = GOLD if both else (P1_COLOR if p1_chose else P2_COLOR)
                draw.rounded_rectangle(
                    (cx_cell + 6, stripe_y, cx_cell + cell_w - 6, cy_cell + cell_h - 6),
                    radius=4, fill=stripe_col,
                )
                if both:
                    pick_lbl = 'both picked'
                elif p1_chose:
                    pick_lbl = p1_name[:14]
                else:
                    pick_lbl = p2_name[:14]
                lbl_bb = draw.textbbox((0, 0), pick_lbl, font=self.font_stat_label)
                lbl_h = lbl_bb[3] - lbl_bb[1]
                lbl_y = stripe_y + (20 - lbl_h) // 2 - lbl_bb[1]
                self._text_center(draw, cx_cell + cell_w // 2, lbl_y,
                                  pick_lbl, self.font_stat_label, (18, 18, 28))

            # ── Star rating — top-right (coloured badge, white icon + text) ───
            sr_str = f'{stars:.2f}'
            sr_bb = draw.textbbox((0, 0), sr_str, font=self.font_stat_label)
            sr_tw = sr_bb[2] - sr_bb[0]
            sr_th = sr_bb[3] - sr_bb[1]
            icon_sz = star_icon.width if star_icon else 0
            icon_gap = 3 if star_icon else 0
            pad_x, pad_y = 5, 3
            badge_w = pad_x + icon_sz + icon_gap + sr_tw + pad_x
            badge_h = sr_th + pad_y * 2 + 2
            badge_x = cx_cell + cell_w - 7 - badge_w
            badge_y = cy_cell + 7
            draw.rounded_rectangle(
                (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
                radius=4, fill=sr_col,
            )
            # Star icon centred vertically inside badge
            icon_y = badge_y + (badge_h - icon_sz) // 2
            if star_icon:
                draw = _paste_icon(img, star_icon, badge_x + pad_x, icon_y)
            # SR text centred vertically inside badge
            text_y = badge_y + pad_y - sr_bb[1]
            draw.text((badge_x + pad_x + icon_sz + icon_gap, text_y),
                      sr_str, font=self.font_stat_label, fill=(255, 255, 255))

            # ── Number circle — bottom-right, raised above pick stripe if picked
            num_r = 11
            num_cx = cx_cell + cell_w - 7 - num_r
            if stripe_y is not None:
                num_cy = stripe_y - num_r - 4
            else:
                num_cy = cy_cell + cell_h - 7 - num_r
            draw.ellipse(
                (num_cx - num_r, num_cy - num_r, num_cx + num_r, num_cy + num_r),
                fill=(50, 50, 72), outline=(90, 90, 120), width=1,
            )
            num_str = str(idx + 1)
            nb = draw.textbbox((0, 0), num_str, font=self.font_stat_label)
            nw = nb[2] - nb[0]
            nh = nb[3] - nb[1]
            draw.text(
                (num_cx - nw // 2 - nb[0], num_cy - nh // 2 - nb[1]),
                num_str, font=self.font_stat_label, fill=(255, 255, 255),
            )

        return self._save(img)

    async def generate_bsk_pick_card_async(self, data: Dict) -> BytesIO:
        """Download map covers + player covers, then render pick card."""
        candidates = data.get('candidates', [])

        # Map covers
        cover_tasks = []
        for m in candidates:
            bsid = m.get('beatmapset_id')
            if bsid:
                url = f"https://assets.ppy.sh/beatmaps/{bsid}/covers/list.jpg"
                cover_tasks.append(download_image(url))
            else:
                cover_tasks.append(_none_coro())

        # Player covers (from cached bytes or URL)
        async def _load_cover(raw: bytes | None, url: str | None):
            if raw:
                try:
                    return Image.open(BytesIO(raw)).convert("RGBA")
                except Exception:
                    logger.debug("bsk_pick_card: raw cover decode failed, falling back to URL", exc_info=True)
            if url:
                r = await download_image(url)
                if r and not isinstance(r, Exception):
                    return r.convert("RGBA")
            return None

        p1_cover_task = _load_cover(data.get('p1_cover_data'), data.get('p1_cover_url'))
        p2_cover_task = _load_cover(data.get('p2_cover_data'), data.get('p2_cover_url'))

        map_results, p1_cover, p2_cover = await asyncio.gather(
            asyncio.gather(*cover_tasks, return_exceptions=True),
            p1_cover_task,
            p2_cover_task,
        )

        covers = [None if isinstance(r, Exception) or r is None else r for r in map_results]
        data = {**data, 'covers': covers, 'p1_cover': p1_cover, 'p2_cover': p2_cover}
        return await asyncio.to_thread(self.generate_bsk_pick_card, data)

    # ─────────────────────────────────────────────────────────────────────────
    # PORTRAIT CARD HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    # Layout constants shared by both pool cards
    _PC_COLS     = 3
    _PC_PAD      = 8          # outer left/right padding
    _PC_GAP      = 8          # gap between cards
    _PC_CELL_W   = (CARD_WIDTH - 2 * _PC_PAD - (_PC_COLS - 1) * _PC_GAP) // _PC_COLS  # 256 px
    _PC_CELL_H   = int(_PC_CELL_W * 1.42)   # ≈ 364 px  (playing-card ratio)
    _PC_RADIUS   = 14         # card corner radius
    _PC_COVER_H  = int(_PC_CELL_H * 0.44)   # ≈ 160 px
    _PC_INFO_H   = _PC_CELL_H - int(_PC_CELL_H * 0.44)

    def _portrait_cell_xy(self, idx: int) -> tuple[int, int]:
        col = idx % self._PC_COLS
        row = idx // self._PC_COLS
        x   = self._PC_PAD + col * (self._PC_CELL_W + self._PC_GAP)
        y   = row * (self._PC_CELL_H + self._PC_GAP)
        return x, y

    def _draw_compact_facedown(
        self, img: Image.Image, cx: int, cy: int, card_w: int, card_h: int,
        is_banned: bool = False,
        is_played: bool = False,
        glow_rgb: Optional[tuple] = None,
        stripe_label: Optional[str] = None,
        stripe_color: Optional[tuple] = None,
        flipped: bool = False,
    ) -> ImageDraw.Draw:
        """Compact face-down card for the group overview (no number, no type strip).

        is_played takes precedence over is_banned visually if both set
        (a played map cannot still be banned).
        """
        r = max(6, card_w // 12)
        if is_banned:
            bg_col = (38, 10, 10)
        elif is_played:
            bg_col = (22, 22, 30)
        else:
            bg_col = (28, 14, 42)

        # ── Build card as RGBA ────────────────────────────────────────────────
        card = Image.new('RGBA', (card_w, card_h), (0, 0, 0, 0))
        ImageDraw.Draw(card).rounded_rectangle(
            (0, 0, card_w - 1, card_h - 1), radius=r, fill=(*bg_col, 255))

        # Subtle inner border
        if not is_banned:
            inset = max(4, card_w // 22)
            ImageDraw.Draw(card).rounded_rectangle(
                (inset, inset, card_w - inset - 1, card_h - inset - 1),
                radius=max(r - 2, 3), outline=(46, 28, 70, 255), width=1,
            )

        # BAN colour overlay (before flip — part of card design)
        if is_banned:
            bov  = Image.new('RGBA', (card_w, card_h), (130, 20, 20, 160))
            card = Image.alpha_composite(card, bov)
        elif is_played:
            # PLAYED: subtle dark grey wash so the card recedes
            pov  = Image.new('RGBA', (card_w, card_h), (10, 10, 16, 130))
            card = Image.alpha_composite(card, pov)

        # Pick stripe at the bottom edge (before flip — ends up at top for P1)
        if stripe_label and stripe_color:
            sy = card_h - 28
            sl = Image.new('RGBA', (card_w, 28), (0, 0, 0, 0))
            ImageDraw.Draw(sl).rounded_rectangle(
                (3, 4, card_w - 4, 24), radius=5, fill=(*stripe_color, 220))
            card.paste(sl, (0, sy), sl)
            cd    = ImageDraw.Draw(card)
            lb_bb = cd.textbbox((0, 0), stripe_label, font=self.font_stat_label)
            lb_y  = sy + 4 + (20 - (lb_bb[3] - lb_bb[1])) // 2 - lb_bb[1]
            cd.text(
                ((card_w - (lb_bb[2] - lb_bb[0])) // 2 - lb_bb[0], lb_y),
                stripe_label, font=self.font_stat_label, fill=(255, 255, 255),
            )

        # Flip 180° for P1 top row (before adding logo/text so they stay upright)
        if flipped:
            card = card.rotate(180)

        # osu! logo watermark — always upright, pasted AFTER optional flip
        logo_sz  = int(card_w * 0.52)
        osu_icon = load_icon('osulogo', size=logo_sz)
        if osu_icon and osu_icon.mode == 'RGBA':
            rc, gc, bc, ac = osu_icon.split()
            if is_banned:
                opacity = 28
            elif is_played:
                opacity = 22
            else:
                opacity = 55
            ac      = ac.point(lambda v: v * opacity // 100)
            dim     = Image.merge('RGBA', (rc, gc, bc, ac))
            lx      = (card_w - logo_sz) // 2
            ly      = (card_h - logo_sz) // 2
            card.paste(dim, (lx, ly), dim)

        # BAN / PLAYED label — upright after flip
        if is_banned:
            cd    = ImageDraw.Draw(card)
            bt    = 'BAN'
            bt_bb = cd.textbbox((0, 0), bt, font=self.font_label)
            cd.text(
                ((card_w - (bt_bb[2] - bt_bb[0])) // 2 - bt_bb[0],
                 (card_h - (bt_bb[3] - bt_bb[1])) // 2 - bt_bb[1]),
                bt, font=self.font_label, fill=(220, 80, 80),
            )
        elif is_played:
            cd    = ImageDraw.Draw(card)
            pt    = '✓'
            pt_bb = cd.textbbox((0, 0), pt, font=self.font_label)
            cd.text(
                ((card_w - (pt_bb[2] - pt_bb[0])) // 2 - pt_bb[0],
                 (card_h - (pt_bb[3] - pt_bb[1])) // 2 - pt_bb[1]),
                pt, font=self.font_label, fill=(140, 200, 140),
            )

        # Paste onto main image
        img.paste(card.convert('RGB'), (cx, cy), card.split()[3])

        # Glow border (drawn on main image after paste)
        if glow_rgb:
            eff_glow = glow_rgb
        elif is_banned:
            eff_glow = (160, 40, 40)
        elif is_played:
            eff_glow = (60, 60, 80)
        else:
            eff_glow = (50, 30, 80)
        strength = 5 if (glow_rgb or is_banned) else (3 if is_played else 1)
        alphas   = [25, 50, 90, 150, 220][:strength]
        for i, alpha in enumerate(alphas):
            off = max(0, strength - 1 - i)
            gl  = Image.new('RGBA', (card_w, card_h), (0, 0, 0, 0))
            ImageDraw.Draw(gl).rounded_rectangle(
                (off, off, card_w - 1 - off, card_h - 1 - off),
                radius=max(r - off, 3), outline=(*eff_glow, alpha), width=1,
            )
            img.paste(gl, (cx, cy), gl)

        return ImageDraw.Draw(img)

    def _draw_card_glow(self, img: Image.Image, cx: int, cy: int,
                        glow_rgb: tuple, strength: int = 5,
                        cell_w: Optional[int] = None,
                        cell_h: Optional[int] = None) -> ImageDraw.Draw:
        """Soft multi-ring glow around a portrait card.

        cell_w/cell_h override the default class-level portrait card size,
        used when the pool DM card switches to a 4-column compact grid.
        """
        cw = cell_w if cell_w is not None else self._PC_CELL_W
        ch = cell_h if cell_h is not None else self._PC_CELL_H
        r  = self._PC_RADIUS
        alphas = [25, 50, 90, 150, 220][:strength]
        for i, alpha in enumerate(alphas):
            off = strength - i
            gl  = Image.new('RGBA', (cw, ch), (0, 0, 0, 0))
            ImageDraw.Draw(gl).rounded_rectangle(
                (off, off, cw - 1 - off, ch - 1 - off),
                radius=max(r - off, 4),
                outline=(*glow_rgb, alpha), width=1,
            )
            img.paste(gl, (cx, cy), gl)
        return ImageDraw.Draw(img)

    def _draw_portrait_face_down(
        self, img: Image.Image, cx: int, cy: int,
        glow_rgb: Optional[tuple] = None,
        card_num: int = 0,
        stripe_label: Optional[str] = None,
        stripe_color: Optional[tuple] = None,
        is_banned: bool = False,
        type_accent: Optional[tuple] = None,
    ) -> ImageDraw.Draw:
        """Render one face-down portrait card (clean osu! logo back)."""
        cw, ch, r = self._PC_CELL_W, self._PC_CELL_H, self._PC_RADIUS

        # ── Back surface ──────────────────────────────────────────────────────
        bg_col = (28, 14, 42) if not is_banned else (38, 10, 10)
        back   = Image.new('RGB', (cw, ch), bg_col)
        bd     = ImageDraw.Draw(back)

        # Subtle inner border ring
        if not is_banned:
            bd.rounded_rectangle((6, 6, cw - 7, ch - 7),
                                  radius=max(r - 4, 4), outline=(46, 28, 70), width=1)

        # Type accent strip at top
        if type_accent and not is_banned:
            bd.rectangle([(0, 0), (cw, 4)], fill=type_accent)

        # Rounded mask
        mask = Image.new('L', (cw, ch), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, cw - 1, ch - 1), radius=r, fill=255)
        img.paste(back, (cx, cy), mask)
        draw = ImageDraw.Draw(img)

        # ── osu! logo — large centered ────────────────────────────────────────
        logo_sz  = int(cw * 0.52)   # ~133 px on a 256 px card
        osu_icon = load_icon('osulogo', size=logo_sz)
        if osu_icon and osu_icon.mode == 'RGBA':
            rc, gc, bc, ac = osu_icon.split()
            opacity = 28 if is_banned else 55   # % alpha
            ac = ac.point(lambda v: v * opacity // 100)
            dim = Image.merge('RGBA', (rc, gc, bc, ac))
            lx  = cx + (cw - logo_sz) // 2
            ly  = cy + (ch - logo_sz) // 2
            img.paste(dim, (lx, ly), dim)
            draw = ImageDraw.Draw(img)

        # ── BAN label ─────────────────────────────────────────────────────────
        if is_banned:
            bt    = 'BAN'
            bt_bb = draw.textbbox((0, 0), bt, font=self.font_row)
            draw.text(
                (cx + (cw - (bt_bb[2]-bt_bb[0])) // 2 - bt_bb[0],
                 cy + (ch - (bt_bb[3]-bt_bb[1])) // 2 - bt_bb[1]),
                bt, font=self.font_row, fill=(220, 80, 80),
            )

        # ── Glow border ───────────────────────────────────────────────────────
        effective_glow = glow_rgb or ((160, 40, 40) if is_banned else (60, 40, 90))
        strength = 5 if glow_rgb or is_banned else 2
        draw = self._draw_card_glow(img, cx, cy, effective_glow, strength)

        # ── Bottom name stripe (when picked) ─────────────────────────────────
        if stripe_label and stripe_color:
            sy = cy + ch - 30
            sl = Image.new('RGBA', (cw, 30), (0, 0, 0, 0))
            ImageDraw.Draw(sl).rounded_rectangle(
                (4, 4, cw - 5, 25), radius=6, fill=(*stripe_color, 230),
            )
            img.paste(sl, (cx, sy), sl)
            draw = ImageDraw.Draw(img)
            lbl_bb = draw.textbbox((0, 0), stripe_label, font=self.font_stat_label)
            lbl_h  = lbl_bb[3] - lbl_bb[1]
            lbl_y  = sy + 4 + (21 - lbl_h) // 2 - lbl_bb[1]
            self._text_center(draw, cx + cw // 2, lbl_y,
                              stripe_label, self.font_stat_label, (255, 255, 255))

        # ── Number circle (bottom-right) ──────────────────────────────────────
        num_r    = 13
        sy_off   = (cy + ch - 34) if stripe_label else (cy + ch - 12 - num_r)
        ncx      = cx + cw - 12 - num_r
        ncy      = sy_off - num_r if stripe_label else sy_off
        circ_bg  = (70, 20, 20) if is_banned else (28, 20, 46)
        circ_out = (160, 50, 50) if is_banned else (80, 60, 120)
        draw.ellipse((ncx-num_r, ncy-num_r, ncx+num_r, ncy+num_r),
                     fill=circ_bg, outline=circ_out, width=1)
        ns = str(card_num)
        nb = draw.textbbox((0, 0), ns, font=self.font_stat_label)
        draw.text(
            (ncx - (nb[2]-nb[0])//2 - nb[0], ncy - (nb[3]-nb[1])//2 - nb[1]),
            ns, font=self.font_stat_label,
            fill=(200, 120, 120) if is_banned else (200, 180, 240),
        )
        return ImageDraw.Draw(img)

    def _draw_portrait_face_up(
        self, img: Image.Image, cx: int, cy: int,
        m: dict,
        cover: Optional[Image.Image],
        is_banned: bool = False,
        is_played: bool = False,
        glow_rgb: Optional[tuple] = None,
        card_num: int = 0,
        cell_w: Optional[int] = None,
        cell_h: Optional[int] = None,
        cover_h_override: Optional[int] = None,
    ) -> ImageDraw.Draw:
        """Render one face-up portrait card (osu! card style).

        is_played adds a desaturated grey overlay with a 'PLAYED' badge to
        signal the map is no longer pickable in the current pool.

        cell_w/cell_h/cover_h_override let the caller shrink the card for
        denser layouts (e.g. 4-column DM grid for an 8-map pool).
        """
        cw       = cell_w if cell_w is not None else self._PC_CELL_W
        ch       = cell_h if cell_h is not None else self._PC_CELL_H
        cover_h  = (cover_h_override
                    if cover_h_override is not None
                    else (self._PC_COVER_H if cell_h is None else int(ch * 0.44)))
        info_y0  = cy + cover_h
        r        = self._PC_RADIUS

        title   = m.get('title',       'Unknown')
        artist  = m.get('artist',      '')
        stars   = m.get('star_rating', 0.0)
        mtype   = m.get('map_type',    '')
        ar_val  = m.get('ar')
        od_val  = m.get('od')
        cs_val  = m.get('cs')
        hp_val  = m.get('hp')
        bpm     = m.get('bpm')
        drain   = m.get('drain_time')

        type_rgb = SKILL_COLORS.get(mtype, (120, 80, 160))
        sr_col   = _sr_color(stars)
        info_bg  = (8, 8, 16)   # darkened panel

        # ── 1. Dark card base (rounded) ───────────────────────────────────────
        base = Image.new('RGB', (cw, ch), info_bg)
        mask = Image.new('L',   (cw, ch), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, cw-1, ch-1), radius=r, fill=255)

        # ── 2. Cover image (top portion) ──────────────────────────────────────
        if cover and not is_banned:
            try:
                cr  = cover_center_crop(cover.convert('RGBA'), cw, cover_h)
                # Global dim + bottom-to-top gradient for readability
                grad = Image.new('RGBA', (cw, cover_h), (0, 0, 0, 0))
                gd   = ImageDraw.Draw(grad)
                for i in range(cover_h):
                    a = int(180 * (i / cover_h) ** 1.4)
                    gd.line([(0, i), (cw, i)], fill=(0, 0, 0, a))
                cr = Image.alpha_composite(cr, grad)
                base.paste(cr.convert('RGB'), (0, 0))
            except Exception:
                base.paste(Image.new('RGB', (cw, cover_h), MTYPE_BG.get(mtype, (28, 28, 44))), (0, 0))
        else:
            base.paste(Image.new('RGB', (cw, cover_h), (40, 14, 14) if is_banned else MTYPE_BG.get(mtype, (28, 28, 44))), (0, 0))

        # Type accent strip at very top (4 px)
        bdrw = ImageDraw.Draw(base)
        bdrw.rectangle([(0, 0), (cw, 4)], fill=type_rgb)

        img.paste(base, (cx, cy), mask)
        draw = ImageDraw.Draw(img)

        # ── 3. Star rating badge (top-right of cover) ─────────────────────────
        star_icon = load_icon('star', size=11)
        sr_str    = f'{stars:.2f}'
        sr_bb     = draw.textbbox((0, 0), sr_str, font=self.font_stat_label)
        sr_tw     = sr_bb[2] - sr_bb[0]
        isz       = star_icon.width if star_icon else 0
        igap      = 3 if star_icon else 0
        bw        = 6 + isz + igap + sr_tw + 6
        bh        = 18
        bx        = cx + cw - 8 - bw
        by_badge  = cy + 10
        draw.rounded_rectangle((bx, by_badge, bx + bw, by_badge + bh),
                                radius=5, fill=sr_col)
        iy_icon = by_badge + (bh - isz) // 2
        if star_icon:
            draw = _paste_icon(img, star_icon, bx + 6, iy_icon)
        draw.text((bx + 6 + isz + igap, by_badge + 2),
                  sr_str, font=self.font_stat_label, fill=(255, 255, 255))

        # ── 4. Info panel ─────────────────────────────────────────────────────
        tx      = cx + 10       # left text margin
        right_x = cx + cw - 10  # right margin
        ty      = info_y0 + 8

        # BPM + timer icons — right-aligned at top two rows of info zone
        timer_icon = load_icon('timer', size=13)
        bpm_icon   = load_icon('bpm',   size=13)
        if drain:
            mm, ss    = divmod(drain, 60)
            drain_str = f'{mm}:{ss:02d}'
            drain_bb  = draw.textbbox((0, 0), drain_str, font=self.font_stat_label)
            drain_tw  = drain_bb[2] - drain_bb[0]
            t_icon_w  = (timer_icon.width + 3) if timer_icon else 0
            draw.text((right_x - drain_tw, ty), drain_str,
                      font=self.font_stat_label, fill=TEXT_SECONDARY)
            if timer_icon:
                draw = _paste_icon(img, timer_icon,
                                   right_x - drain_tw - t_icon_w, ty + 1)
        if bpm:
            bpm_str  = f'{int(bpm)}'
            bpm_bb   = draw.textbbox((0, 0), bpm_str, font=self.font_stat_label)
            bpm_tw   = bpm_bb[2] - bpm_bb[0]
            b_icon_w = (bpm_icon.width + 3) if bpm_icon else 0
            bpm_ty   = ty + 18
            draw.text((right_x - bpm_tw, bpm_ty), bpm_str,
                      font=self.font_stat_label, fill=TEXT_SECONDARY)
            if bpm_icon:
                draw = _paste_icon(img, bpm_icon,
                                   right_x - bpm_tw - b_icon_w, bpm_ty + 1)

        # Title (left, same row as timer)
        disp_title = title if len(title) <= 18 else title[:17] + '…'
        draw.text((tx, ty), disp_title, font=self.font_label, fill=TEXT_PRIMARY)
        ty += 20

        # Artist (left, same row as BPM)
        disp_art = artist if len(artist) <= 22 else artist[:21] + '…'
        draw.text((tx, ty), disp_art, font=self.font_small, fill=TEXT_SECONDARY)
        ty += 18

        # Separator
        draw.line([(tx, ty), (cx + cw - 10, ty)], fill=(36, 36, 56), width=1)
        ty += 7

        # Version name (left) + mtype badge (right)
        version = m.get('version', '')
        if version:
            disp_ver = version if len(version) <= 18 else version[:17] + '…'
            draw.text((tx, ty), disp_ver, font=self.font_stat_label, fill=(110, 135, 185))
        if mtype:
            mtype_lbl = MTYPE_FULL.get(mtype, mtype.upper())
            mtype_col = SKILL_COLORS.get(mtype, (120, 80, 160))
            lbl_bb    = draw.textbbox((0, 0), mtype_lbl, font=self.font_stat_label)
            lbl_w     = lbl_bb[2] - lbl_bb[0]
            badge_rx  = right_x
            badge_lx  = badge_rx - lbl_w - 10
            draw.rounded_rectangle(
                (badge_lx, ty - 1, badge_rx, ty + 13),
                radius=3, fill=mtype_col,
            )
            draw.text((badge_lx + 5, ty), mtype_lbl, font=self.font_stat_label, fill=(18, 18, 28))
        ty += 20

        # Stat bars (CS / AR / OD / HP) — full width, tall bars
        bar_right  = cx + cw - 10
        bar_h_px   = 8
        bar_bg     = (36, 36, 56)
        bar_fill   = (220, 60, 100)   # osu! pink-red
        row_h      = 22

        for label, val in [('CS', cs_val), ('AR', ar_val), ('OD', od_val), ('HP', hp_val)]:
            if val is None:
                continue
            val_str  = f'{int(val)}' if float(val) == int(val) else f'{val:.1f}'
            draw.text((tx, ty + 1), label, font=self.font_stat_label, fill=(150, 150, 190))
            val_x    = tx + 26
            v_bb     = draw.textbbox((0, 0), val_str, font=self.font_stat_label)
            val_tw   = v_bb[2] - v_bb[0]
            draw.text((val_x, ty + 1), val_str, font=self.font_stat_label, fill=TEXT_PRIMARY)
            bar_x    = val_x + val_tw + 6
            bar_w    = bar_right - bar_x
            fill_w   = max(2, int(bar_w * min(val / 10.0, 1.0)))
            draw.rounded_rectangle((bar_x, ty + 2, bar_right, ty + 2 + bar_h_px),
                                    radius=3, fill=bar_bg)
            draw.rounded_rectangle((bar_x, ty + 2, bar_x + fill_w, ty + 2 + bar_h_px),
                                    radius=3, fill=bar_fill)
            ty += row_h

        # ── 5. Glow border ────────────────────────────────────────────────────
        if glow_rgb:
            effective_glow = glow_rgb
        elif is_banned:
            effective_glow = (160, 40, 40)
        elif is_played:
            effective_glow = (70, 70, 90)
        else:
            effective_glow = (80, 50, 130)
        strength = 5 if (glow_rgb or is_banned) else (3 if is_played else 2)
        draw = self._draw_card_glow(img, cx, cy, effective_glow, strength,
                                    cell_w=cw, cell_h=ch)

        # ── 6. BAN / PLAYED overlay ───────────────────────────────────────────
        if is_banned:
            bov = Image.new('RGBA', (cw, ch), (130, 20, 20, 175))
            img.paste(bov.convert('RGB'), (cx, cy), bov)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((0+cx, 0+cy, cw-1+cx, ch-1+cy),
                                    radius=r, outline=(210, 50, 50), width=2)
            bt    = 'BAN'
            bt_bb = draw.textbbox((0, 0), bt, font=self.font_row)
            draw.text(
                (cx + (cw - (bt_bb[2]-bt_bb[0])) // 2 - bt_bb[0],
                 cy + (ch - (bt_bb[3]-bt_bb[1])) // 2 - bt_bb[1]),
                bt, font=self.font_row, fill=(255, 255, 255),
            )
        elif is_played:
            pov = Image.new('RGBA', (cw, ch), (10, 10, 18, 165))
            img.paste(pov.convert('RGB'), (cx, cy), pov)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((0+cx, 0+cy, cw-1+cx, ch-1+cy),
                                    radius=r, outline=(120, 130, 160), width=2)
            pt    = 'PLAYED'
            pt_bb = draw.textbbox((0, 0), pt, font=self.font_row)
            draw.text(
                (cx + (cw - (pt_bb[2]-pt_bb[0])) // 2 - pt_bb[0],
                 cy + (ch - (pt_bb[3]-pt_bb[1])) // 2 - pt_bb[1]),
                pt, font=self.font_row, fill=(220, 230, 250),
            )

        # ── 7. Number circle (bottom-right) ───────────────────────────────────
        num_r    = 13
        ncx_c    = cx + cw - 12 - num_r
        ncy_c    = cy + ch - 12 - num_r
        if is_banned:
            circ_bg, circ_out, num_fill = (70, 20, 20), (160, 50, 50), (200, 120, 120)
        elif is_played:
            circ_bg, circ_out, num_fill = (28, 28, 38), (110, 120, 150), (180, 190, 210)
        else:
            circ_bg, circ_out, num_fill = (22, 16, 38), (90, 70, 140), (200, 180, 240)
        draw.ellipse((ncx_c-num_r, ncy_c-num_r, ncx_c+num_r, ncy_c+num_r),
                     fill=circ_bg, outline=circ_out, width=1)
        ns = str(card_num)
        nb = draw.textbbox((0, 0), ns, font=self.font_stat_label)
        draw.text(
            (ncx_c - (nb[2]-nb[0])//2 - nb[0], ncy_c - (nb[3]-nb[1])//2 - nb[1]),
            ns, font=self.font_stat_label,
            fill=num_fill,
        )
        return ImageDraw.Draw(img)

    # ─────────────────────────────────────────────────────────────────────────
    # POOL GROUP CARD  (group chat — face-down cards during ban/pick phase)
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_pool_group_card(self, data: Dict) -> BytesIO:
        """
        Shown in the group chat.
        P1 (red) row at top — cards flipped 180°.
        P2 (blue) row at bottom — cards normal orientation.
        Both rows show the same shared pool of 6 face-down cards.
        No type accent strips, no number circles.

        data keys:
          round_number, p1_name, p2_name, p1_country, p2_country
          phase           str       : 'ban' | 'pick'
          p1_ready        bool      : finished banning / picked
          p2_ready        bool      : finished banning / picked
          p1_picked       int|None  : beatmap_id P1 picked
          p2_picked       int|None  : beatmap_id P2 picked
          candidates      list[dict]: beatmap_id, map_type
          banned_ids      list[int] : beatmap_ids banned
          played_ids      list[int] : beatmap_ids already used in earlier rounds
          pick_turn_name  str|None  : whose turn during pick phase (shown in subheader)
        """
        # Group pool card uses a narrower canvas — content is just a header,
        # two small card rows and two name bars.  Keeps the ban/pick overview
        # compact in the chat list.
        W = 540

        n_cards = max(len(data.get('candidates', [])), 6)
        # Compact card dimensions  (deck/fan overlap effect, scales with pool size)
        _cw      = 64
        _ch      = int(_cw * 1.42)                # 91 px
        _ov      = 22                              # overlap between adjacent cards
        _row_w   = n_cards * _cw - (n_cards - 1) * _ov  # total visual row width
        _start_x = max(PADDING_X, (W - _row_w) // 2)    # centred row start x

        # Vertical layout
        header_h  = 36
        name_h    = 28                             # player name bar height
        outer_v   = 10   # top/bottom outer padding of grid zone
        inner_v   = 8    # gap between each card row and the centre divider
        grid_h    = name_h + outer_v + _ch + inner_v + 1 + inner_v + _ch + outer_v + name_h
        H = header_h + grid_h

        img, draw = self._create_canvas(W, H)

        round_num  = data.get('round_number', 1)
        p1_name    = data.get('p1_name', 'P1')
        p2_name    = data.get('p2_name', 'P2')
        p1_country = data.get('p1_country', '')
        p2_country = data.get('p2_country', '')
        p1_picked  = data.get('p1_picked')
        p2_picked  = data.get('p2_picked')
        candidates = data.get('candidates', [])
        banned_ids = set(data.get('banned_ids', []))
        played_ids = set(data.get('played_ids', []))
        phase      = data.get('phase', 'pick')
        p1_ready   = data.get('p1_ready', False)
        p2_ready   = data.get('p2_ready', False)
        turn_name  = data.get('pick_turn_name')

        if phase == 'ban':
            phase_label = 'Ban Phase'
        elif turn_name:
            phase_label = f'Pick Phase · {turn_name} picks'
        else:
            phase_label = 'Pick Phase'
        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL',
                          f'Round {round_num} · {phase_label}', W)

        # ── Player name colours (green = done) ────────────────────────────────
        if phase == 'ban':
            p1_col = ACCENT_GREEN if p1_ready else P1_COLOR
            p2_col = ACCENT_GREEN if p2_ready else P2_COLOR
        else:
            p1_col = ACCENT_GREEN if p1_picked else P1_COLOR
            p2_col = ACCENT_GREEN if p2_picked else P2_COLOR

        # ── Y positions ───────────────────────────────────────────────────────
        y_body  = header_h
        y_p1bar = y_body                                        # P1 name bar
        y_p1    = y_p1bar + name_h + outer_v                    # P1 card row
        y_ctr   = y_p1 + _ch + inner_v                          # centre divider
        y_p2    = y_ctr + 1 + inner_v                            # P2 card row
        y_p2bar = y_p2 + _ch + outer_v                           # P2 name bar

        # ── P1 name bar (red bg, centred) ─────────────────────────────────────
        p1_bar_bg = (50, 20, 20) if p1_col == P1_COLOR else (20, 44, 20)
        draw.rectangle([(0, y_p1bar), (W, y_p1bar + name_h)], fill=p1_bar_bg)
        p1_ny = y_p1bar + (name_h - 14) // 2
        draw = _draw_name_with_flag(img, draw, W // 2, p1_ny,
                                    p1_name, p1_country, self.font_stat_label,
                                    p1_col, align='right_ltr', flag_h=14)
        # shift to true centre: measure block and re-draw centred
        p1_bb = draw.textbbox((0, 0), p1_name, font=self.font_stat_label)
        p1_nw = p1_bb[2] - p1_bb[0]
        flag_obj = load_flag(p1_country, height=14) if p1_country else None
        p1_fw = (flag_obj.width + 6) if flag_obj else 0
        p1_block = p1_fw + p1_nw
        p1_lx = (W - p1_block) // 2
        draw.rectangle([(0, y_p1bar), (W, y_p1bar + name_h)], fill=p1_bar_bg)
        draw = _draw_name_with_flag(img, draw, p1_lx, p1_ny,
                                    p1_name, p1_country, self.font_stat_label,
                                    p1_col, align='left', flag_h=14)

        # ── Centre divider line ───────────────────────────────────────────────
        draw.line([(PADDING_X, y_ctr), (W - PADDING_X, y_ctr)],
                  fill=(60, 50, 90), width=1)

        # ── Card rows (drawn right-to-left so leftmost card is on top) ──────────
        n_visible = max(len(candidates), 6)
        for idx in range(n_visible - 1, -1, -1):
            m   = candidates[idx] if idx < len(candidates) else None
            bid = m.get('beatmap_id') if m else None

            is_ban    = (bid in banned_ids) if bid else False
            is_played = (bid in played_ids) if bid else False
            p1_chose  = (p1_picked == bid) if bid else False
            p2_chose  = (p2_picked == bid) if bid else False

            cx_card = _start_x + idx * (_cw - _ov)

            # P1 row — top, flipped
            p1_glow    = (160, 40, 40) if is_ban else (P1_COLOR if p1_chose else None)
            p1_stripe  = (p1_name[:14] if p1_chose else None)
            p1_stripe_c = (P1_COLOR if p1_chose else None)
            draw = self._draw_compact_facedown(
                img, cx_card, y_p1, _cw, _ch,
                is_banned=is_ban,
                is_played=is_played and not is_ban,
                glow_rgb=p1_glow,
                stripe_label=p1_stripe,
                stripe_color=p1_stripe_c,
                flipped=True,
            )

            # P2 row — bottom, normal
            p2_glow    = (160, 40, 40) if is_ban else (P2_COLOR if p2_chose else None)
            p2_stripe  = (p2_name[:14] if p2_chose else None)
            p2_stripe_c = (P2_COLOR if p2_chose else None)
            draw = self._draw_compact_facedown(
                img, cx_card, y_p2, _cw, _ch,
                is_banned=is_ban,
                is_played=is_played and not is_ban,
                glow_rgb=p2_glow,
                stripe_label=p2_stripe,
                stripe_color=p2_stripe_c,
                flipped=False,
            )

        # ── P2 name bar (blue bg, centred) ────────────────────────────────────
        p2_bar_bg = (16, 24, 50) if p2_col == P2_COLOR else (20, 44, 20)
        draw.rectangle([(0, y_p2bar), (W, y_p2bar + name_h)], fill=p2_bar_bg)
        p2_ny = y_p2bar + (name_h - 14) // 2
        p2_bb = draw.textbbox((0, 0), p2_name, font=self.font_stat_label)
        p2_nw = p2_bb[2] - p2_bb[0]
        flag_obj2 = load_flag(p2_country, height=14) if p2_country else None
        p2_fw = (flag_obj2.width + 6) if flag_obj2 else 0
        p2_block = p2_fw + p2_nw
        p2_lx = (W - p2_block) // 2
        draw = _draw_name_with_flag(img, draw, p2_lx, p2_ny,
                                    p2_name, p2_country, self.font_stat_label,
                                    p2_col, align='left', flag_h=14)

        return self._save(img)

    async def generate_bsk_pool_group_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_bsk_pool_group_card, data)

    # ─────────────────────────────────────────────────────────────────────────
    # POOL DM CARD  (player's private view — detailed maps, ban/pick actions)
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_pool_dm_card(self, data: Dict) -> BytesIO:
        """
        Sent privately to each player during the ban/pick phase.
        Shows all 6 maps as face-up osu!-style portrait cards;
        banned cards receive a red overlay with 'BAN' text.

        data keys:
          round_number, player_name, player_country
          phase          str        : 'ban' | 'pick'
          priority       bool       : pick phase — this is the active player's turn
          banned_ids     list[int]  : beatmap_ids already banned (shown with overlay)
          played_ids     list[int]  : beatmap_ids already used in earlier rounds
          ban_count      int        : how many bans this player has used so far
          max_bans       int        : max bans allowed (default 3)
          candidates     list[dict] :
            beatmap_id, beatmapset_id, title, artist, version,
            star_rating, map_type, ar, od, cs, hp, bpm, drain_time
          covers         list[PIL.Image|None]  — pre-fetched, same order as candidates
        """
        candidates     = data.get('candidates', [])
        n_cards        = len(candidates)
        # Pick a column count: 3 cols for ≤6 maps (existing layout),
        # 4 cols when the pool is bigger (7-8 maps).
        cols           = 3 if n_cards <= 6 else 4
        rows           = max(1, (n_cards + cols - 1) // cols) if n_cards else 2
        rows           = max(rows, 2)

        W        = CARD_WIDTH
        cell_w   = (W - 2 * self._PC_PAD - (cols - 1) * self._PC_GAP) // cols
        cell_h   = int(cell_w * 1.42)
        cover_h  = int(cell_h * 0.44)

        header_h = 36
        player_h = 64    # taller bar to host the profile cover background
        phase_h  = 14    # slim divider strip — no text per the new design
        grid_h   = rows * cell_h + (rows - 1) * self._PC_GAP
        footer_h = 34
        H = header_h + player_h + phase_h + grid_h + footer_h

        img, draw = self._create_canvas(W, H)
        round_num      = data.get('round_number', 1)
        player_name    = data.get('player_name', 'Player')
        player_country = data.get('player_country', '')
        player_cover   = data.get('player_cover')
        phase          = data.get('phase', 'pick')
        priority       = data.get('priority', False)
        banned_ids     = set(data.get('banned_ids', []))
        played_ids     = set(data.get('played_ids', []))
        covers         = data.get('covers', [])

        phase_label = 'Ban Phase' if phase == 'ban' else 'Pick Phase'
        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL',
                          f'Round {round_num} · {phase_label}', W)

        # ── Player bar (centred name + flag, optional profile cover bg) ─────
        y_player = header_h
        # Base fill in case cover fails
        bar_base = (30, 14, 14) if phase == 'ban' else (14, 22, 44)
        draw.rectangle([(0, y_player), (W, y_player + player_h)], fill=bar_base)
        if player_cover:
            try:
                cr = cover_center_crop(player_cover.convert('RGBA'), W, player_h)
                # Strong dim so the white text reads well
                dim = Image.new('RGBA', (W, player_h), (0, 0, 0, 165))
                cr  = Image.alpha_composite(cr, dim)
                # Phase tint
                tint_col = (90, 20, 20, 70) if phase == 'ban' else (20, 30, 70, 70)
                cr = Image.alpha_composite(cr, Image.new('RGBA', (W, player_h), tint_col))
                img.paste(cr.convert('RGB'), (0, y_player), cr.split()[3])
                draw = ImageDraw.Draw(img)
            except Exception:
                logger.debug("bsk_pool_dm_card: player background composite failed", exc_info=True)

        # Centred name + flag block
        flag_h = 18
        flag_obj = load_flag(player_country, height=flag_h) if player_country else None
        flag_w   = flag_obj.width if flag_obj else 0
        gap      = 8 if flag_obj else 0
        name_bbox = draw.textbbox((0, 0), player_name, font=self.font_label)
        name_w = name_bbox[2] - name_bbox[0]
        name_h = name_bbox[3] - name_bbox[1]
        block_w  = flag_w + gap + name_w
        block_x  = (W - block_w) // 2
        block_y  = y_player + (player_h - max(name_h, flag_h)) // 2
        if flag_obj:
            draw = _paste_icon(img, flag_obj, block_x,
                               block_y + (name_h - flag_h) // 2)
            draw.text((block_x + flag_w + gap, block_y),
                      player_name, font=self.font_label, fill=TEXT_PRIMARY)
        else:
            draw.text((block_x, block_y),
                      player_name, font=self.font_label, fill=TEXT_PRIMARY)

        # Bottom hairline accent
        accent_col = (150, 60, 60) if phase == 'ban' else (90, 110, 200)
        draw.line([(0, y_player + player_h - 1), (W, y_player + player_h - 1)],
                  fill=accent_col, width=1)

        # ── Phase divider strip (intentionally textless) ─────────────────────
        y_phase = y_player + player_h
        phase_bg = (24, 12, 12) if phase == 'ban' else (12, 18, 36)
        draw.rectangle([(0, y_phase), (W, y_phase + phase_h)], fill=phase_bg)

        # ── Face-up portrait card grid (shifted 5 px up) ────────────────────────
        y_grid     = y_phase + phase_h - 5
        cells_total = cols * rows
        for idx in range(cells_total):
            col_i = idx % cols
            row_i = idx // cols
            cx = self._PC_PAD + col_i * (cell_w + self._PC_GAP)
            cy = y_grid + row_i * (cell_h + self._PC_GAP)

            if idx >= n_cards:
                # empty slot — neutral placeholder
                draw.rounded_rectangle(
                    (cx, cy, cx + cell_w, cy + cell_h),
                    radius=self._PC_RADIUS, fill=(22, 22, 35),
                )
                continue

            m         = candidates[idx]
            bid       = m.get('beatmap_id')
            is_ban    = bid in banned_ids
            is_played = bid in played_ids and not is_ban
            cover     = covers[idx] if idx < len(covers) else None

            draw = self._draw_portrait_face_up(
                img, cx, cy, m, cover,
                is_banned=is_ban,
                is_played=is_played,
                glow_rgb=None,
                card_num=idx + 1,
                cell_w=cell_w,
                cell_h=cell_h,
                cover_h_override=cover_h,
            )

        # ── Footer ────────────────────────────────────────────────────────────
        y_footer = H - footer_h
        draw.rectangle([(0, y_footer), (W, H)], fill=HEADER_BG)
        # Ban phase has no footer text per the new clean layout — caption + buttons
        # carry the instructions.  Pick phase keeps a tiny status hint.
        if phase != 'ban':
            ft = 'Your turn — pick a map' if priority else 'Waiting for opponent to pick…'
            self._text_center(draw, W // 2, y_footer + 10, ft, self.font_stat_label, ACCENT_GREEN)

        return self._save(img)

    async def generate_bsk_pool_dm_card_async(self, data: Dict) -> BytesIO:
        """Download map covers + player profile cover, then render the DM card."""
        candidates = data.get('candidates', [])
        cover_tasks = []
        for m in candidates:
            bsid = m.get('beatmapset_id')
            if bsid:
                url = f"https://assets.ppy.sh/beatmaps/{bsid}/covers/list.jpg"
                cover_tasks.append(download_image(url))
            else:
                cover_tasks.append(_none_coro())

        async def _load_player_cover(raw, url):
            if raw:
                try:
                    return Image.open(BytesIO(raw)).convert("RGBA")
                except Exception:
                    logger.debug("bsk_pool_dm_card: raw player cover decode failed, falling back to URL", exc_info=True)
            if url:
                r = await download_image(url)
                return r.convert("RGBA") if r else None
            return None

        map_results, player_cover = await asyncio.gather(
            asyncio.gather(*cover_tasks, return_exceptions=True),
            _load_player_cover(data.get('player_cover_data'),
                               data.get('player_cover_url')),
        )
        covers = [None if isinstance(r, Exception) or r is None else r for r in map_results]
        data   = {**data, 'covers': covers, 'player_cover': player_cover}
        return await asyncio.to_thread(self.generate_bsk_pool_dm_card, data)

    # ─────────────────────────────────────────────────────────────────────────
    # ROUND START CARD  (VS layout)
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_round_start_card(self, data: Dict) -> BytesIO:
        """Active round card: live scoreboard + detailed map panel + component losses."""
        W = CARD_WIDTH
        header_h = 58
        scoreboard_h = 126
        map_h = 128
        edge_h = 0
        H = header_h + scoreboard_h + map_h + edge_h

        img, draw = self._create_canvas(W, H)

        def fmt_score(value) -> str:
            try:
                return f"{int(value):,}".replace(",", " ")
            except Exception:
                return "0"

        def fmt_delta(value) -> str:
            try:
                return f"{abs(int(round(value))):,}".replace(",", ".")
            except Exception:
                return "0"

        def fmt_duration(seconds) -> str:
            try:
                seconds = int(seconds or 0)
            except Exception:
                seconds = 0
            if seconds <= 0:
                return ""
            mins, secs = divmod(seconds, 60)
            return f"{mins}:{secs:02d}"

        def text_w(text: str, font) -> int:
            bb = draw.textbbox((0, 0), text, font=font)
            return bb[2] - bb[0]

        def truncate(text: str, font, max_w: int) -> str:
            text = str(text or "")
            if text_w(text, font) <= max_w:
                return text
            ell = "…"
            while text and text_w(text + ell, font) > max_w:
                text = text[:-1]
            return (text + ell) if text else ell

        def draw_pill(x: int, y: int, label: str, color, *, font=None, pad_x=10, pad_y=5, fill_scale=0.30):
            font = font or self.font_stat_label
            bb = draw.textbbox((0, 0), label, font=font)
            tw = bb[2] - bb[0]
            th = bb[3] - bb[1]
            w = tw + pad_x * 2
            h = th + pad_y * 2
            bg = tuple(max(0, int(c * fill_scale)) for c in color)
            draw.rounded_rectangle((x, y, x + w, y + h), radius=8, fill=bg, outline=color, width=1)
            draw.text((x + pad_x, y + pad_y - bb[1]), label, font=font, fill=TEXT_PRIMARY)
            return w, h

        def draw_center_pill(cx: int, y: int, label: str, color, *, font=None, pad_x=12, pad_y=5):
            font = font or self.font_stat_label
            bb = draw.textbbox((0, 0), label, font=font)
            w = (bb[2] - bb[0]) + pad_x * 2
            draw_pill(cx - w // 2, y, label, color, font=font, pad_x=pad_x, pad_y=pad_y)
            return w

        round_num = int(data.get('round_number', 1) or 1)
        max_rounds = data.get('max_rounds')
        mode = str(data.get('mode') or 'casual').upper()
        mult = float(data.get('round_multiplier') or 1.0)
        p1_name = str(data.get('p1_name', 'P1') or 'P1')
        p2_name = str(data.get('p2_name', 'P2') or 'P2')
        p1_country = data.get('p1_country', '')
        p2_country = data.get('p2_country', '')
        score_p1 = int(data.get('score_p1', 0) or 0)
        score_p2 = int(data.get('score_p2', 0) or 0)
        p1_round_wins = int(data.get('p1_round_wins', 0) or 0)
        p2_round_wins = int(data.get('p2_round_wins', 0) or 0)
        stars = float(data.get('star_rating', 0.0) or 0.0)
        map_type = str(data.get('map_type') or '').lower()
        if map_type not in SKILL_KEYS:
            map_type = 'mixed'
        map_label = MTYPE_FULL.get(map_type, 'MIXED')
        map_color = SKILL_COLORS.get(map_type, (120, 130, 160))

        # ── Header ────────────────────────────────────────────────────────────
        draw.rectangle((0, 0, W, header_h), fill=HEADER_BG)
        draw.rectangle((0, 0, W, 3), fill=map_color)
        draw.text((PADDING_X, 16), 'LIVE DUEL', font=self.font_label, fill=TEXT_PRIMARY)
        draw.text((PADDING_X, 38), mode, font=self.font_stat_label, fill=TEXT_SECONDARY)

        round_str = f'ROUND {round_num}' + (f' / {int(max_rounds)}' if max_rounds else '')
        self._text_right(draw, W - PADDING_X, 14, round_str, self.font_label, TEXT_PRIMARY)

        rounds_badge = f'{p1_round_wins} — {p2_round_wins}'
        rb_bb = draw.textbbox((0, 0), rounds_badge, font=self.font_label)
        rb_w = (rb_bb[2] - rb_bb[0]) + 30
        rb_h = (rb_bb[3] - rb_bb[1]) + 10
        rb_x = W // 2 - rb_w // 2
        rb_y = (header_h - rb_h) // 2
        draw.rounded_rectangle((rb_x, rb_y, rb_x + rb_w, rb_y + rb_h), radius=10, fill=(0, 0, 0))
        draw.text((rb_x + 15, rb_y + 5 - rb_bb[1]), rounds_badge, font=self.font_label, fill=TEXT_PRIMARY)

        # ── Scoreboard ────────────────────────────────────────────────────────
        y_scoreboard = header_h
        half = W // 2
        # player backgrounds
        img.paste(Image.new('RGB', (half, scoreboard_h), (46, 18, 24)), (0, y_scoreboard))
        img.paste(Image.new('RGB', (W - half, scoreboard_h), (16, 28, 58)), (half, y_scoreboard))
        draw = ImageDraw.Draw(img)
        # inner player panels
        draw.rounded_rectangle((PADDING_X - 8, y_scoreboard + 12, half - 55, y_scoreboard + scoreboard_h - 14), radius=14, fill=(58, 22, 28))
        draw.rounded_rectangle((half + 55, y_scoreboard + 12, W - PADDING_X + 8, y_scoreboard + scoreboard_h - 14), radius=14, fill=(20, 34, 70))
        draw.rectangle((half - 1, y_scoreboard, half + 1, y_scoreboard + scoreboard_h), fill=(34, 38, 58))

        vs_icon = load_icon('versus', size=42)
        if vs_icon:
            draw = _paste_icon(img, vs_icon, half - vs_icon.width // 2, y_scoreboard + 24)
        else:
            self._text_center(draw, half, y_scoreboard + 30, 'VS', self.font_vs, TEXT_SECONDARY)
        # multiplier badge below VS icon
        draw_center_pill(half, y_scoreboard + 96, f'MULTIPLIER: {mult:.2f}x', GOLD, font=self.font_stat_label, pad_x=12, pad_y=5)

        p1_panel_cx = (PADDING_X - 8 + half - 55) // 2
        p2_panel_cx = (half + 55 + W - PADDING_X + 8) // 2

        name_y = y_scoreboard + 28
        p1_name_text = truncate(p1_name, self.font_row, 240)
        p2_name_text = truncate(p2_name, self.font_row, 240)
        p1_flag = load_flag(p1_country, height=18) if p1_country else None
        p2_flag = load_flag(p2_country, height=18) if p2_country else None
        p1_gap = 6 if p1_flag else 0
        p2_gap = 6 if p2_flag else 0
        p1_name_w = text_w(p1_name_text, self.font_row)
        p2_name_w = text_w(p2_name_text, self.font_row)
        p1_block_w = p1_name_w + (p1_flag.width if p1_flag else 0) + p1_gap
        p2_block_w = p2_name_w + (p2_flag.width if p2_flag else 0) + p2_gap
        p1_left = p1_panel_cx - p1_block_w // 2
        p2_left = p2_panel_cx - p2_block_w // 2
        if p1_flag:
            draw = _paste_icon(img, p1_flag, p1_left, name_y + 4)
        draw.text((p1_left + (p1_flag.width if p1_flag else 0) + p1_gap, name_y), p1_name_text, font=self.font_row, fill=P1_COLOR)
        draw.text((p2_left, name_y), p2_name_text, font=self.font_row, fill=P2_COLOR)
        if p2_flag:
            draw = _paste_icon(img, p2_flag, p2_left + p2_name_w + p2_gap, name_y + 4)

        self._text_center(draw, p1_panel_cx, y_scoreboard + 54, fmt_score(score_p1), self.font_big, TEXT_PRIMARY)
        self._text_center(draw, p2_panel_cx, y_scoreboard + 54, fmt_score(score_p2), self.font_big, TEXT_PRIMARY)

        lead = score_p1 - score_p2
        if lead > 0:
            self._text_center(draw, p1_panel_cx, y_scoreboard + 86, f'+{fmt_delta(lead)}', self.font_label, P1_COLOR)
            self._text_center(draw, p2_panel_cx, y_scoreboard + 86, f'-{fmt_delta(lead)}', self.font_label, P2_COLOR)
        elif lead < 0:
            self._text_center(draw, p1_panel_cx, y_scoreboard + 86, f'-{fmt_delta(lead)}', self.font_label, P1_COLOR)
            self._text_center(draw, p2_panel_cx, y_scoreboard + 86, f'+{fmt_delta(lead)}', self.font_label, P2_COLOR)
        else:
            self._text_center(draw, p1_panel_cx, y_scoreboard + 86, '±0', self.font_label, GOLD)
            self._text_center(draw, p2_panel_cx, y_scoreboard + 86, '±0', self.font_label, GOLD)

        # ── Map block ────────────────────────────────────────────────────────
        y_map = header_h + scoreboard_h
        map_cover = data.get('map_cover')
        if map_cover:
            try:
                cropped = cover_center_crop(map_cover.convert('RGBA'), W, map_h)
                overlay = Image.new('RGBA', (W, map_h), (0, 0, 0, 178))
                blended = Image.alpha_composite(cropped, overlay)
                img.paste(blended.convert('RGB'), (0, y_map))
                draw = ImageDraw.Draw(img)
            except Exception:
                draw.rectangle((0, y_map, W, y_map + map_h), fill=(10, 12, 22))
        else:
            draw.rectangle((0, y_map, W, y_map + map_h), fill=(10, 12, 22))
        # card-like map bg panel
        draw.rounded_rectangle((PADDING_X - 8, y_map + 10, W - PADDING_X + 8, y_map + map_h - 10), radius=14, fill=(8, 11, 22))
        draw.rectangle((0, y_map, W, y_map + 2), fill=(35, 40, 62))

        artist = str(data.get('beatmap_artist', '') or '')
        title_name = str(data.get('beatmap_name', '') or data.get('beatmap_title', 'Unknown Map') or 'Unknown Map')
        mapper = str(data.get('beatmap_creator', '') or data.get('creator', '') or data.get('mapper', '') or '')
        version = str(data.get('beatmap_version', '') or '')

        title = truncate(title_name, self.font_label, W - 2 * PADDING_X - 40)
        self._text_center(draw, W // 2, y_map + 18, title, self.font_label, TEXT_PRIMARY)
        if artist:
            self._text_center(draw, W // 2, y_map + 40, truncate(artist, self.font_stat_label, W - 2 * PADDING_X - 40), self.font_stat_label, TEXT_SECONDARY)

        diff_mapper = version or 'Unknown diff'
        if mapper:
            diff_mapper = f'{diff_mapper} | {mapper}'
        self._text_center(draw, W // 2, y_map + 59, truncate(diff_mapper, self.font_stat_label, W - 2 * PADDING_X - 60), self.font_stat_label, (140, 160, 205))

        # meta row with icons
        meta_items = []
        star_icon = load_icon('star', size=13)
        bpm_icon = load_icon('bpm', size=13)
        timer_icon = load_icon('timer', size=13)
        meta_items.append((star_icon, f'{stars:.2f}'))
        bpm = data.get('bpm')
        if bpm:
            meta_items.append((bpm_icon, f'{float(bpm):.0f}'))
        dur = fmt_duration(data.get('length_seconds'))
        if dur:
            meta_items.append((timer_icon, dur))
        sep = '   '
        total_w = 0
        parts = []
        for icon, txt in meta_items:
            iw = icon.width + 4 if icon else 0
            tw = text_w(txt, self.font_stat_label)
            parts.append((icon, txt, iw + tw))
            total_w += iw + tw
        total_w += text_w(sep, self.font_stat_label) * max(0, len(parts) - 1)
        cx = W // 2 - total_w // 2
        meta_y = y_map + 81
        for idx, (icon, txt, _w) in enumerate(parts):
            if idx:
                draw.text((cx, meta_y), sep, font=self.font_stat_label, fill=TEXT_SECONDARY)
                cx += text_w(sep, self.font_stat_label)
            if icon:
                draw = _paste_icon(img, icon, int(cx), meta_y + 1)
                cx += icon.width + 4
            draw.text((cx, meta_y), txt, font=self.font_stat_label, fill=TEXT_SECONDARY)
            cx += text_w(txt, self.font_stat_label)

        comp_badge = map_label.split()[0] if map_label else 'MIXED'
        cb_font = self.font_stat_label
        cb_bb = draw.textbbox((0, 0), comp_badge, font=cb_font)
        cb_w = (cb_bb[2] - cb_bb[0]) + 30
        cb_h = (cb_bb[3] - cb_bb[1]) + 10
        cb_x = W // 2 - cb_w // 2
        cb_y = y_map + 102
        draw.rounded_rectangle((cb_x, cb_y, cb_x + cb_w, cb_y + cb_h), radius=8, fill=map_color)
        draw.text((cb_x + 15, cb_y + 5 - cb_bb[1]), comp_badge, font=cb_font, fill=(18, 18, 28))

        return self._save(img)

    async def generate_bsk_round_start_card_async(self, data: Dict) -> BytesIO:
        """Download map cover then render round start card."""
        map_cover = None
        bsid = data.get('beatmapset_id')
        if bsid:
            url = f"https://assets.ppy.sh/beatmaps/{bsid}/covers/cover.jpg"
            result = await download_image(url)
            if result and not isinstance(result, Exception):
                map_cover = result
        data = {**data, 'map_cover': map_cover}
        return await asyncio.to_thread(self.generate_bsk_round_start_card, data)

    # ─────────────────────────────────────────────────────────────────────────
    # ROUND RESULT CARD
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_round_result_card(self, data: Dict) -> BytesIO:
        """Finished round card: compact result + per-round stats + duel totals."""
        W = CARD_WIDTH
        header_h = 58
        result_h = 76
        scoreboard_h = 118
        stats_h = 132
        H = header_h + result_h + scoreboard_h + stats_h

        img, draw = self._create_canvas(W, H)

        def fmt_score(value) -> str:
            try:
                return f"{int(value):,}".replace(",", " ")
            except Exception:
                return "0"

        def fmt_delta(value) -> str:
            try:
                return f"{abs(int(round(value))):,}".replace(",", ".")
            except Exception:
                return "0"

        def text_w(text: str, font) -> int:
            bb = draw.textbbox((0, 0), str(text), font=font)
            return bb[2] - bb[0]

        def truncate(text: str, font, max_w: int) -> str:
            text = str(text or "")
            if text_w(text, font) <= max_w:
                return text
            ell = "…"
            while text and text_w(text + ell, font) > max_w:
                text = text[:-1]
            return (text + ell) if text else ell

        def draw_centered_name(cx: int, y: int, name: str, country: str, color, *, max_w: int = 250):
            name = truncate(name, self.font_row, max_w)
            flag = load_flag(country, height=18) if country else None
            gap = 6 if flag else 0
            name_w = text_w(name, self.font_row)
            flag_w = flag.width if flag else 0
            block_w = flag_w + gap + name_w
            x = cx - block_w // 2
            if flag:
                _paste_icon(img, flag, x, y + 4)
                x += flag_w + gap
            draw.text((x, y), name, font=self.font_row, fill=color)

        round_num = int(data.get('round_number', 1) or 1)
        mode = str(data.get('mode') or 'DUEL').upper()
        p1_name = str(data.get('p1_name', 'P1') or 'P1')
        p2_name = str(data.get('p2_name', 'P2') or 'P2')
        p1_country = data.get('p1_country', '')
        p2_country = data.get('p2_country', '')
        winner = int(data.get('winner', 0) or 0)
        winner_col = P1_COLOR if winner == 1 else (P2_COLOR if winner == 2 else GOLD)
        winner_name = (p1_name if winner == 1 else p2_name) if winner else None
        winner_country = (p1_country if winner == 1 else p2_country) if winner else ''

        p1_points = int(data.get('p1_points', 0) or 0)
        p2_points = int(data.get('p2_points', 0) or 0)
        p1_acc = float(data.get('p1_acc', 0.0) or 0.0)
        p2_acc = float(data.get('p2_acc', 0.0) or 0.0)
        p1_combo = int(data.get('p1_combo', 0) or 0)
        p2_combo = int(data.get('p2_combo', 0) or 0)
        p1_misses = int(data.get('p1_misses', 0) or 0)
        p2_misses = int(data.get('p2_misses', 0) or 0)
        score_p1 = int(data.get('score_p1', 0) or 0)
        score_p2 = int(data.get('score_p2', 0) or 0)

        # ── Header ────────────────────────────────────────────────────────────
        draw.rectangle((0, 0, W, header_h), fill=HEADER_BG)
        draw.rectangle((0, 0, W, 3), fill=winner_col)
        draw.text((PADDING_X, 14), 'ROUND RESULT', font=self.font_label, fill=TEXT_PRIMARY)
        draw.text((PADDING_X, 38), mode, font=self.font_stat_label, fill=TEXT_SECONDARY)
        self._text_right(draw, W - PADDING_X, 14, f'ROUND {round_num}', self.font_label, TEXT_PRIMARY)
        self._text_right(draw, W - PADDING_X, 38, 'COMPLETED', self.font_stat_label, TEXT_SECONDARY)

        # ── Winner banner ─────────────────────────────────────────────────────
        y_result = header_h
        banner_bg = (44, 18, 24) if winner == 1 else ((16, 28, 58) if winner == 2 else HEADER_BG)
        draw.rectangle((0, y_result, W, y_result + result_h), fill=banner_bg)
        draw.rectangle((0, y_result, W, y_result + 2), fill=winner_col)
        if winner_name:
            self._text_center(draw, W // 2, y_result + 8, 'WINNER', self.font_stat_label, TEXT_SECONDARY)
            flag_obj = load_flag(winner_country, height=22) if winner_country else None
            name = truncate(winner_name, self.font_big, 520)
            flag_w = flag_obj.width + 10 if flag_obj else 0
            name_w = text_w(name, self.font_big)
            nx = W // 2 - (flag_w + name_w) // 2
            if flag_obj:
                draw = _paste_icon(img, flag_obj, nx, y_result + 34)
                nx += flag_obj.width + 10
            draw.text((nx, y_result + 27), name, font=self.font_big, fill=winner_col)
            draw.rectangle((0, y_result + result_h - 4, W, y_result + result_h), fill=winner_col)
        else:
            self._text_center(draw, W // 2, y_result + 24, 'ROUND DRAW', self.font_big, GOLD)

        # ── Round points scoreboard ───────────────────────────────────────────
        y_score = header_h + result_h
        half = W // 2
        img.paste(Image.new('RGB', (half, scoreboard_h), (46, 18, 24)), (0, y_score))
        img.paste(Image.new('RGB', (W - half, scoreboard_h), (16, 28, 58)), (half, y_score))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((PADDING_X - 8, y_score + 12, half - 55, y_score + scoreboard_h - 14), radius=14, fill=(58, 22, 28))
        draw.rounded_rectangle((half + 55, y_score + 12, W - PADDING_X + 8, y_score + scoreboard_h - 14), radius=14, fill=(20, 34, 70))
        draw.rectangle((half - 1, y_score, half + 1, y_score + scoreboard_h), fill=(34, 38, 58))

        p1_panel_cx = (PADDING_X - 8 + half - 55) // 2
        p2_panel_cx = (half + 55 + W - PADDING_X + 8) // 2
        draw_centered_name(p1_panel_cx, y_score + 24, p1_name, p1_country, P1_COLOR)
        draw_centered_name(p2_panel_cx, y_score + 24, p2_name, p2_country, P2_COLOR)

        self._text_center(draw, p1_panel_cx, y_score + 51, fmt_score(p1_points), self.font_big, TEXT_PRIMARY)
        self._text_center(draw, p2_panel_cx, y_score + 51, fmt_score(p2_points), self.font_big, TEXT_PRIMARY)
        point_lead = p1_points - p2_points
        if point_lead > 0:
            self._text_center(draw, p1_panel_cx, y_score + 85, f'+{fmt_delta(point_lead)}', self.font_label, P1_COLOR)
            self._text_center(draw, p2_panel_cx, y_score + 85, f'-{fmt_delta(point_lead)}', self.font_label, P2_COLOR)
        elif point_lead < 0:
            self._text_center(draw, p1_panel_cx, y_score + 85, f'-{fmt_delta(point_lead)}', self.font_label, P1_COLOR)
            self._text_center(draw, p2_panel_cx, y_score + 85, f'+{fmt_delta(point_lead)}', self.font_label, P2_COLOR)
        else:
            self._text_center(draw, p1_panel_cx, y_score + 85, '±0', self.font_label, GOLD)
            self._text_center(draw, p2_panel_cx, y_score + 85, '±0', self.font_label, GOLD)

        vs_icon = load_icon('versus', size=38)
        if vs_icon:
            draw = _paste_icon(img, vs_icon, half - vs_icon.width // 2, y_score + 38)
        else:
            self._text_center(draw, half, y_score + 43, 'VS', self.font_vs, TEXT_SECONDARY)

        # ── Player stat cells ─────────────────────────────────────────────────
        y_stats = header_h + result_h + scoreboard_h
        draw.rectangle((0, y_stats, W, y_stats + stats_h), fill=(8, 10, 18))
        draw.rectangle((0, y_stats, W, y_stats + 2), fill=(35, 40, 62))

        col_gap = 8
        row_gap = 8
        cell_w = (W - 2 * PADDING_X - col_gap * 2) // 3
        cell_h = 48
        row1_y = y_stats + 12
        row2_y = row1_y + cell_h + row_gap

        p1_cells = [
            ('COMBO', f'{p1_combo:,}x'.replace(',', ' '), p1_combo, p2_combo, True),
            ('ACCURACY', f'{p1_acc:.2f}%', p1_acc, p2_acc, True),
            ('MISSES', str(p1_misses), p1_misses, p2_misses, False),
        ]
        p2_cells = [
            ('COMBO', f'{p2_combo:,}x'.replace(',', ' '), p2_combo, p1_combo, True),
            ('ACCURACY', f'{p2_acc:.2f}%', p2_acc, p1_acc, True),
            ('MISSES', str(p2_misses), p2_misses, p1_misses, False),
        ]

        def stat_color(value, other, higher_better: bool):
            if value == other:
                return GOLD
            return ACCENT_GREEN if (value > other) == higher_better else (190, 70, 70)

        def draw_value_badge(cx: int, y: int, value: str, color):
            display = truncate(value, self.font_stat_label, cell_w - 18)
            bb = draw.textbbox((0, 0), display, font=self.font_stat_label)
            tw = bb[2] - bb[0]
            th = bb[3] - bb[1]
            pad_x = 4
            pad_y = 2
            bw = tw + pad_x * 2
            bh = th + pad_y * 2 + 1
            bx = cx - bw // 2
            by = y
            draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=4, fill=color)
            self._text_center(draw, cx, by + pad_y - bb[1], display, self.font_stat_label, (255, 255, 255))

        def draw_stat_row(cells, y: int, accent):
            for i, (label, value, numeric, other, higher_better) in enumerate(cells):
                x = PADDING_X + i * (cell_w + col_gap)
                fill = tuple(max(0, int(c * 0.28)) for c in accent)
                draw.rounded_rectangle((x, y, x + cell_w, y + cell_h), radius=10, fill=fill, outline=accent, width=1)
                draw.rounded_rectangle((x + 1, y + 1, x + cell_w - 1, y + 5), radius=2, fill=accent)
                self._text_center(draw, x + cell_w // 2, y + 10, label, self.font_stat_label, TEXT_PRIMARY)
                draw_value_badge(x + cell_w // 2, y + 26, value, stat_color(numeric, other, higher_better))

        draw_stat_row(p1_cells, row1_y, P1_COLOR)
        draw_stat_row(p2_cells, row2_y, P2_COLOR)

        return self._save(img)

    async def generate_bsk_round_result_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_bsk_round_result_card, data)

    # ─────────────────────────────────────────────────────────────────────────
    # DUEL END CARD
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_duel_end_card(self, data: Dict) -> BytesIO:
        """Final duel card: compact esports-style match result."""
        W = CARD_WIDTH
        header_h = 58
        victory_h = 82
        scoreboard_h = 126
        summary_h = 70
        flow_h = 70
        H = header_h + victory_h + scoreboard_h + summary_h + flow_h

        img, draw = self._create_canvas(W, H)

        def fmt_score(value) -> str:
            try:
                return f"{int(value):,}".replace(",", " ")
            except Exception:
                return "0"

        def fmt_delta_int(value) -> str:
            try:
                return f"{abs(int(round(value))):,}".replace(",", ".")
            except Exception:
                return "0"

        def fmt_rating_delta(value) -> str:
            if value is None:
                return "—"
            try:
                value = float(value)
            except Exception:
                return "—"
            return f"+{value:.1f}" if value >= 0 else f"{value:.1f}"

        def text_w(text: str, font) -> int:
            bb = draw.textbbox((0, 0), text, font=font)
            return bb[2] - bb[0]

        def truncate(text: str, font, max_w: int) -> str:
            text = str(text or "")
            if text_w(text, font) <= max_w:
                return text
            ell = "…"
            while text and text_w(text + ell, font) > max_w:
                text = text[:-1]
            return (text + ell) if text else ell

        def draw_pill(x: int, y: int, label: str, color, *, font=None, pad_x=10, pad_y=5, bright=False):
            font = font or self.font_stat_label
            bb = draw.textbbox((0, 0), label, font=font)
            tw = bb[2] - bb[0]
            th = bb[3] - bb[1]
            w = tw + pad_x * 2
            h = th + pad_y * 2
            if bright:
                fill = color
                text_col = (18, 18, 28)
                outline = None
            else:
                fill = tuple(max(0, int(c * 0.30)) for c in color)
                text_col = TEXT_PRIMARY
                outline = color
            if outline:
                draw.rounded_rectangle((x, y, x + w, y + h), radius=8, fill=fill, outline=outline, width=1)
            else:
                draw.rounded_rectangle((x, y, x + w, y + h), radius=8, fill=fill)
            draw.text((x + pad_x, y + pad_y - bb[1]), label, font=font, fill=text_col)
            return w, h

        p1_name = str(data.get('p1_name', 'P1') or 'P1')
        p2_name = str(data.get('p2_name', 'P2') or 'P2')
        p1_country = data.get('p1_country', '')
        p2_country = data.get('p2_country', '')
        winner = int(data.get('winner', 0) or 0)
        score_p1 = int(data.get('score_p1', 0) or 0)
        score_p2 = int(data.get('score_p2', 0) or 0)
        mode = str(data.get('mode', 'casual') or 'casual').upper()
        total_rounds = int(data.get('total_rounds', 0) or 0)
        is_test = bool(data.get('is_test', False))
        rounds = data.get('rounds', []) or []

        winner_name = (p1_name if winner == 1 else p2_name) if winner else None
        loser_name = (p2_name if winner == 1 else p1_name) if winner else None
        winner_col = P1_COLOR if winner == 1 else (P2_COLOR if winner == 2 else GOLD)
        winner_country = (p1_country if winner == 1 else p2_country) if winner else ''
        p1_wins = sum(1 for r in rounds if int(r.get('winner', 0) or 0) == 1)
        p2_wins = sum(1 for r in rounds if int(r.get('winner', 0) or 0) == 2)

        # ── Header ────────────────────────────────────────────────────────────
        draw.rectangle((0, 0, W, header_h), fill=HEADER_BG)
        draw.rectangle((0, 0, W, 3), fill=winner_col)
        draw.text((PADDING_X, 14), 'DUEL COMPLETE', font=self.font_label, fill=TEXT_PRIMARY)
        draw.text((PADDING_X, 38), mode + (' · TEST' if is_test else ''), font=self.font_stat_label, fill=TEXT_SECONDARY)
        rounds_meta = f'{total_rounds or len(rounds)} ROUNDS'
        self._text_right(draw, W - PADDING_X, 14, rounds_meta, self.font_label, TEXT_PRIMARY)
        self._text_right(draw, W - PADDING_X, 38, 'FINAL RESULT', self.font_stat_label, TEXT_SECONDARY)

        rounds_badge = f'{p1_wins} — {p2_wins}' if rounds else f'{total_rounds}R'
        rb_bb = draw.textbbox((0, 0), rounds_badge, font=self.font_label)
        rb_w = (rb_bb[2] - rb_bb[0]) + 30
        rb_h = (rb_bb[3] - rb_bb[1]) + 10
        rb_x = W // 2 - rb_w // 2
        rb_y = (header_h - rb_h) // 2
        draw.rounded_rectangle((rb_x, rb_y, rb_x + rb_w, rb_y + rb_h), radius=10, fill=(0, 0, 0))
        draw.text((rb_x + 15, rb_y + 5 - rb_bb[1]), rounds_badge, font=self.font_label, fill=TEXT_PRIMARY)

        # ── Victory banner ───────────────────────────────────────────────────
        y_victory = header_h
        banner_bg = (44, 18, 24) if winner == 1 else ((16, 28, 58) if winner == 2 else HEADER_BG)
        draw.rectangle((0, y_victory, W, y_victory + victory_h), fill=banner_bg)
        draw.rectangle((0, y_victory, W, y_victory + 2), fill=winner_col)

        if winner_name:
            self._text_center(draw, W // 2, y_victory + 10, 'VICTORY', self.font_stat_label, TEXT_SECONDARY)
            flag_obj = load_flag(winner_country, height=22) if winner_country else None
            name = truncate(winner_name, self.font_big, 520)
            flag_w = flag_obj.width + 10 if flag_obj else 0
            name_w = text_w(name, self.font_big)
            nx = W // 2 - (flag_w + name_w) // 2
            if flag_obj:
                draw = _paste_icon(img, flag_obj, nx, y_victory + 34)
                nx += flag_obj.width + 10
            draw.text((nx, y_victory + 28), name, font=self.font_big, fill=winner_col)
            draw.rectangle((0, y_victory + victory_h - 4, W, y_victory + victory_h), fill=winner_col)
        else:
            self._text_center(draw, W // 2, y_victory + 28, 'DUEL DRAW', self.font_big, GOLD)

        # ── Scoreboard ────────────────────────────────────────────────────────
        y_score = header_h + victory_h
        half = W // 2
        img.paste(Image.new('RGB', (half, scoreboard_h), (46, 18, 24)), (0, y_score))
        img.paste(Image.new('RGB', (W - half, scoreboard_h), (16, 28, 58)), (half, y_score))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle((PADDING_X - 8, y_score + 12, half - 55, y_score + scoreboard_h - 14), radius=14, fill=(58, 22, 28))
        draw.rounded_rectangle((half + 55, y_score + 12, W - PADDING_X + 8, y_score + scoreboard_h - 14), radius=14, fill=(20, 34, 70))
        draw.rectangle((half - 1, y_score, half + 1, y_score + scoreboard_h), fill=(34, 38, 58))

        vs_icon = load_icon('versus', size=42)

        if vs_icon:
            draw = _paste_icon(img, vs_icon, half - vs_icon.width // 2, y_score + 44)
        else:
            self._text_center(draw, half, y_score + 50, 'VS', self.font_vs, TEXT_SECONDARY)

        p1_panel_cx = (PADDING_X - 8 + half - 55) // 2
        p2_panel_cx = (half + 55 + W - PADDING_X + 8) // 2

        name_y = y_score + 28
        p1_name_text = truncate(p1_name, self.font_row, 240)
        p2_name_text = truncate(p2_name, self.font_row, 240)
        p1_flag = load_flag(p1_country, height=18) if p1_country else None
        p2_flag = load_flag(p2_country, height=18) if p2_country else None
        p1_gap = 6 if p1_flag else 0
        p2_gap = 6 if p2_flag else 0
        p1_name_w = text_w(p1_name_text, self.font_row)
        p2_name_w = text_w(p2_name_text, self.font_row)
        p1_block_w = p1_name_w + (p1_flag.width if p1_flag else 0) + p1_gap
        p2_block_w = p2_name_w + (p2_flag.width if p2_flag else 0) + p2_gap
        p1_left = p1_panel_cx - p1_block_w // 2
        p2_left = p2_panel_cx - p2_block_w // 2
        if p1_flag:
            draw = _paste_icon(img, p1_flag, p1_left, name_y + 4)
        draw.text((p1_left + (p1_flag.width if p1_flag else 0) + p1_gap, name_y), p1_name_text, font=self.font_row, fill=P1_COLOR)
        draw.text((p2_left, name_y), p2_name_text, font=self.font_row, fill=P2_COLOR)
        if p2_flag:
            draw = _paste_icon(img, p2_flag, p2_left + p2_name_w + p2_gap, name_y + 4)
        self._text_center(draw, p1_panel_cx, y_score + 58, fmt_score(score_p1), self.font_big, TEXT_PRIMARY)
        self._text_center(draw, p2_panel_cx, y_score + 58, fmt_score(score_p2), self.font_big, TEXT_PRIMARY)

        lead = score_p1 - score_p2
        if lead > 0:
            self._text_center(draw, p1_panel_cx, y_score + 90, f'+{fmt_delta_int(lead)}', self.font_label, P1_COLOR)
            self._text_center(draw, p2_panel_cx, y_score + 90, f'-{fmt_delta_int(lead)}', self.font_label, P2_COLOR)
        elif lead < 0:
            self._text_center(draw, p1_panel_cx, y_score + 90, f'-{fmt_delta_int(lead)}', self.font_label, P1_COLOR)
            self._text_center(draw, p2_panel_cx, y_score + 90, f'+{fmt_delta_int(lead)}', self.font_label, P2_COLOR)
        else:
            self._text_center(draw, p1_panel_cx, y_score + 90, '±0', self.font_label, GOLD)
            self._text_center(draw, p2_panel_cx, y_score + 90, '±0', self.font_label, GOLD)

        # ── Summary: compact per-player rating components ──────────────────────────
        y_summary = header_h + victory_h + scoreboard_h
        draw.rectangle((0, y_summary, W, y_summary + summary_h), fill=(8, 10, 18))
        draw.rectangle((0, y_summary, W, y_summary + 2), fill=(35, 40, 62))

        self._text_center(draw, W // 2, y_summary + summary_h // 2 - 7, 'RATING CHANGE', self.font_stat_label, TEXT_SECONDARY)

        full_labels = {
            'aim': 'AIM',
            'speed': 'SPEED',
            'acc': 'ACCURACY',
            'cons': 'CONSISTENCY',
        }

        def rating_badge(label: str, value, col):
            return f"{label}: {fmt_rating_delta(value)}", col

        def draw_rating_side(player_key: str, x: int, align: str):
            rows = [
                ('aim', 'speed'),
                ('acc', 'cons'),
            ]
            for row_i, comps in enumerate(rows):
                items = [rating_badge(full_labels[c], data.get(f'{player_key}_delta_{c}'), SKILL_COLORS[c]) for c in comps]
                gap = 6
                widths = []
                for txt, _col in items:
                    bb = draw.textbbox((0, 0), txt, font=self.font_stat_label)
                    widths.append((bb[2] - bb[0]) + 14)
                total = sum(widths) + gap * (len(widths) - 1)
                cx = x if align == 'left' else x - total
                y = y_summary + 14 + row_i * 25
                for (txt, col), bw in zip(items, widths):
                    draw_pill(cx, y, txt, col, font=self.font_stat_label, pad_x=7, pad_y=3, bright=True)
                    cx += bw + gap

        draw_rating_side('p1', PADDING_X, 'left')
        draw_rating_side('p2', W - PADDING_X, 'right')

        # ── Round flow ────────────────────────────────────────────────────────
        y_flow = header_h + victory_h + scoreboard_h + summary_h
        draw.rectangle((0, y_flow, W, y_flow + flow_h), fill=HEADER_BG)
        self._text_center(draw, W // 2, y_flow + 8, 'ROUND FLOW', self.font_stat_label, TEXT_SECONDARY)

        if rounds:
            labels = []
            for i, rnd in enumerate(rounds):
                rnum = int(rnd.get('round_number', i + 1) or (i + 1))
                rw = int(rnd.get('winner', 0) or 0)
                if rw == 1:
                    col = P1_COLOR
                elif rw == 2:
                    col = P2_COLOR
                else:
                    col = GOLD
                labels.append((f'R{rnum}', col, True))

            # Fit into one or two centered rows.
            rows: list[list[tuple[str, tuple, bool]]] = [[]]
            cur_w = 0
            max_row_w = W - 2 * PADDING_X
            pill_gap = 8
            for item in labels:
                label = item[0]
                w = text_w(label, self.font_stat_label) + 18
                if rows[-1] and cur_w + pill_gap + w > max_row_w:
                    rows.append([])
                    cur_w = 0
                rows[-1].append(item)
                cur_w += w + (pill_gap if cur_w else 0)

            start_y = y_flow + 30 if len(rows) == 1 else y_flow + 26
            for row_i, row in enumerate(rows[:2]):
                row_widths = [text_w(label, self.font_stat_label) + 18 for label, _col, _bright in row]
                row_w = sum(row_widths) + pill_gap * max(0, len(row_widths) - 1)
                x = W // 2 - row_w // 2
                y = start_y + row_i * 24
                for (label, col, bright), pw in zip(row, row_widths):
                    draw_pill(x, y, label, col, font=self.font_stat_label, pad_x=9, pad_y=3, bright=bright)
                    x += pw + pill_gap
        else:
            self._text_center(draw, W // 2, y_flow + 34, 'No round history', self.font_stat_label, TEXT_SECONDARY)

        return self._save(img)

    async def generate_bsk_duel_end_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_bsk_duel_end_card, data)
