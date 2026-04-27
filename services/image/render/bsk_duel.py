"""BSK duel phase card renderers."""

import asyncio
from io import BytesIO
from typing import Dict, List, Optional

from PIL import Image, ImageDraw

from services.image.constants import (
    BG_COLOR, HEADER_BG, ROW_EVEN, ROW_ODD,
    TEXT_PRIMARY, TEXT_SECONDARY,
    ACCENT_RED, ACCENT_GREEN, PANEL_BG,
    PADDING_X, CARD_WIDTH,
)
from services.image.utils import load_icon, load_flag, download_image, cover_center_crop, _none_coro

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
        draw = _paste_icon(img, flag, fx, y + max(0, flag_mid_offset))
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
                    pass

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
                    pick_lbl = 'оба выбрали'
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
        from io import BytesIO as _BytesIO

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
                    return Image.open(_BytesIO(raw)).convert("RGBA")
                except Exception:
                    pass
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
        glow_rgb: Optional[tuple] = None,
        stripe_label: Optional[str] = None,
        stripe_color: Optional[tuple] = None,
        flipped: bool = False,
    ) -> ImageDraw.Draw:
        """Compact face-down card for the group overview (no number, no type strip)."""
        r = max(6, card_w // 12)
        bg_col = (28, 14, 42) if not is_banned else (38, 10, 10)

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
            opacity = 28 if is_banned else 55
            ac      = ac.point(lambda v: v * opacity // 100)
            dim     = Image.merge('RGBA', (rc, gc, bc, ac))
            lx      = (card_w - logo_sz) // 2
            ly      = (card_h - logo_sz) // 2
            card.paste(dim, (lx, ly), dim)

        # BAN label — upright after flip
        if is_banned:
            cd    = ImageDraw.Draw(card)
            bt    = 'BAN'
            bt_bb = cd.textbbox((0, 0), bt, font=self.font_label)
            cd.text(
                ((card_w - (bt_bb[2] - bt_bb[0])) // 2 - bt_bb[0],
                 (card_h - (bt_bb[3] - bt_bb[1])) // 2 - bt_bb[1]),
                bt, font=self.font_label, fill=(220, 80, 80),
            )

        # Paste onto main image
        img.paste(card.convert('RGB'), (cx, cy), card.split()[3])
        draw = ImageDraw.Draw(img)

        # Glow border (drawn on main image after paste)
        eff_glow = glow_rgb or ((160, 40, 40) if is_banned else (50, 30, 80))
        strength = 5 if (glow_rgb or is_banned) else 1
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
                        glow_rgb: tuple, strength: int = 5) -> ImageDraw.Draw:
        """Soft multi-ring glow around a portrait card."""
        cw, ch, r = self._PC_CELL_W, self._PC_CELL_H, self._PC_RADIUS
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
        glow_rgb: Optional[tuple] = None,
        card_num: int = 0,
    ) -> ImageDraw.Draw:
        """Render one face-up portrait card (osu! card style)."""
        cw, ch      = self._PC_CELL_W, self._PC_CELL_H
        cover_h     = self._PC_COVER_H
        info_y0     = cy + cover_h
        r           = self._PC_RADIUS

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
                grad = Image.new('RGBA', (cw, cover_h), (0, 0, 0, 120))  # base dark tint
                gd   = ImageDraw.Draw(grad)
                fade = 80   # pixels of gradient
                for i in range(fade):
                    a = int(220 * (i / fade) ** 1.6)
                    y = cover_h - fade + i
                    gd.line([(0, y), (cw, y)], fill=(0, 0, 0, a))
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
            mtype_lbl = SKILL_LABELS.get(mtype, mtype.upper())
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
        effective_glow = glow_rgb or ((160, 40, 40) if is_banned else (80, 50, 130))
        strength = 5 if glow_rgb or is_banned else 2
        draw = self._draw_card_glow(img, cx, cy, effective_glow, strength)

        # ── 6. BAN overlay ────────────────────────────────────────────────────
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

        # ── 7. Number circle (bottom-right) ───────────────────────────────────
        num_r    = 13
        ncx_c    = cx + cw - 12 - num_r
        ncy_c    = cy + ch - 12 - num_r
        circ_bg  = (70, 20, 20) if is_banned else (22, 16, 38)
        circ_out = (160, 50, 50) if is_banned else (90, 70, 140)
        draw.ellipse((ncx_c-num_r, ncy_c-num_r, ncx_c+num_r, ncy_c+num_r),
                     fill=circ_bg, outline=circ_out, width=1)
        ns = str(card_num)
        nb = draw.textbbox((0, 0), ns, font=self.font_stat_label)
        draw.text(
            (ncx_c - (nb[2]-nb[0])//2 - nb[0], ncy_c - (nb[3]-nb[1])//2 - nb[1]),
            ns, font=self.font_stat_label,
            fill=(200, 120, 120) if is_banned else (200, 180, 240),
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
          phase          str       : 'ban' | 'pick'
          p1_ready       bool      : finished banning / picked
          p2_ready       bool      : finished banning / picked
          p1_picked      int|None  : beatmap_id P1 picked
          p2_picked      int|None  : beatmap_id P2 picked
          candidates     list[dict]: beatmap_id, map_type
          banned_ids     list[int] : beatmap_ids banned
        """
        W = CARD_WIDTH

        # Compact 6-per-row card dimensions  (deck/fan overlap effect)
        _cw      = 100
        _ch      = int(_cw * 1.42)                # 142 px
        _ov      = 14                              # overlap between adjacent cards
        _row_w   = 6 * _cw - 5 * _ov             # 530 px — total visual row width
        _start_x = (W - _row_w) // 2             # centred row start x

        # Vertical layout
        header_h  = 36
        status_h  = 40
        outer_v   = 14   # top/bottom outer padding of grid zone
        inner_v   = 10   # gap between each card row and the centre divider
        grid_h    = outer_v + _ch + inner_v + 1 + inner_v + _ch + outer_v
        footer_h  = 30
        H = header_h + status_h + grid_h + footer_h

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
        phase      = data.get('phase', 'pick')
        p1_ready   = data.get('p1_ready', False)
        p2_ready   = data.get('p2_ready', False)

        phase_label = 'Ban Phase' if phase == 'ban' else 'Pick Phase'
        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL',
                          f'Round {round_num} · {phase_label}', W)

        # ── Status bar ────────────────────────────────────────────────────────
        y_status = header_h
        draw.rectangle([(0, y_status), (W, y_status + status_h)], fill=HEADER_BG)

        if phase == 'ban':
            p1_col = ACCENT_GREEN if p1_ready else P1_COLOR
            p2_col = ACCENT_GREEN if p2_ready else P2_COLOR
            p1_sub = '✓ ready' if p1_ready else 'banning...'
            p2_sub = '✓ ready' if p2_ready else 'banning...'
        else:
            p1_col = ACCENT_GREEN if p1_picked else P1_COLOR
            p2_col = ACCENT_GREEN if p2_picked else P2_COLOR
            p1_sub = '✓ picked' if p1_picked else 'thinking...'
            p2_sub = '✓ picked' if p2_picked else 'thinking...'

        name_y = y_status + (status_h - 16) // 2
        draw = _draw_name_with_flag(img, draw, PADDING_X, name_y,
                                    p1_name, p1_country, self.font_label,
                                    p1_col, align='left', flag_h=16)
        draw = _draw_name_with_flag(img, draw, W - PADDING_X, name_y,
                                    p2_name, p2_country, self.font_label,
                                    p2_col, align='right', flag_h=16)
        center_txt = f'{p1_sub}   |   {p2_sub}'
        self._text_center(draw, W // 2, name_y, center_txt,
                          self.font_stat_label, TEXT_SECONDARY)

        # ── Y positions ───────────────────────────────────────────────────────
        y_grid  = header_h + status_h
        y_p1    = y_grid + outer_v                  # P1 row (top, flipped)
        y_ctr   = y_p1 + _ch + inner_v             # centre divider
        y_p2    = y_ctr + 1 + inner_v              # P2 row (bottom, normal)

        # Centre divider line (thin, dim)
        draw.line([(PADDING_X, y_ctr), (W - PADDING_X, y_ctr)],
                  fill=(60, 50, 90), width=1)

        # ── Card rows (drawn right-to-left so leftmost card is on top) ──────────
        for idx in range(5, -1, -1):
            m   = candidates[idx] if idx < len(candidates) else None
            bid = m.get('beatmap_id') if m else None

            is_ban   = (bid in banned_ids) if bid else False
            p1_chose = (p1_picked == bid) if bid else False
            p2_chose = (p2_picked == bid) if bid else False

            cx_card = _start_x + idx * (_cw - _ov)

            # P1 row — top, flipped
            p1_glow    = (160, 40, 40) if is_ban else (P1_COLOR if p1_chose else None)
            p1_stripe  = (p1_name[:14] if p1_chose else None)
            p1_stripe_c = (P1_COLOR if p1_chose else None)
            draw = self._draw_compact_facedown(
                img, cx_card, y_p1, _cw, _ch,
                is_banned=is_ban,
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
                glow_rgb=p2_glow,
                stripe_label=p2_stripe,
                stripe_color=p2_stripe_c,
                flipped=False,
            )

        # ── Footer ────────────────────────────────────────────────────────────
        y_footer = H - footer_h
        draw.rectangle([(0, y_footer), (W, H)], fill=HEADER_BG)
        if phase == 'ban':
            if p1_ready and p2_ready:
                ft = 'Both finished banning — picks start!'
            else:
                ft = 'Players are banning maps (via DM)'
        elif p1_picked and p2_picked:
            ft = 'Both picked — resolving the round!'
        elif p1_picked or p2_picked:
            ft = 'One player picked — waiting for the other...'
        else:
            ft = 'Players are picking a map (via DM)'
        self._text_center(draw, W // 2, y_footer + 8, ft,
                          self.font_stat_label, TEXT_SECONDARY)

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
        banned cards receive a red overlay with «БАН» text.

        data keys:
          round_number, player_name, player_country
          phase          str        : 'ban' | 'pick'
          priority       bool       : pick phase — this player picks first (lower rating)
          banned_ids     list[int]  : beatmap_ids already banned (shown with overlay)
          ban_count      int        : how many bans this player has used so far
          max_bans       int        : max bans allowed (default 3)
          candidates     list[dict] :
            beatmap_id, beatmapset_id, title, artist, version,
            star_rating, map_type, ar, od, cs, hp, bpm, drain_time
          covers         list[PIL.Image|None]  — pre-fetched, same order as candidates
        """
        W        = CARD_WIDTH
        header_h = 36
        player_h = 40    # player name + phase tag
        phase_h  = 32    # instruction strip
        grid_h   = 2 * self._PC_CELL_H + self._PC_GAP   # 736 px
        footer_h = 34
        H = header_h + player_h + phase_h + grid_h + footer_h

        img, draw = self._create_canvas(W, H)
        round_num      = data.get('round_number', 1)
        player_name    = data.get('player_name', 'Player')
        player_country = data.get('player_country', '')
        phase          = data.get('phase', 'pick')
        priority       = data.get('priority', False)
        banned_ids     = set(data.get('banned_ids', []))
        ban_count      = data.get('ban_count', 0)
        max_bans       = data.get('max_bans', 3)
        candidates     = data.get('candidates', [])
        covers         = data.get('covers', [])

        phase_label = 'Ban Phase' if phase == 'ban' else 'Pick Phase'
        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL',
                          f'Round {round_num} · {phase_label}', W)

        # ── Player bar ────────────────────────────────────────────────────────
        y_player = header_h
        draw.rectangle([(0, y_player), (W, y_player + player_h)], fill=HEADER_BG)
        py = y_player + (player_h - 16) // 2
        draw = _draw_name_with_flag(img, draw, PADDING_X, py,
                                    player_name, player_country,
                                    self.font_label, TEXT_PRIMARY,
                                    align='left', flag_h=16)
        if phase == 'ban':
            tag_txt = f'🚫  Ban {ban_count}/{max_bans} — toggle maps below'
            self._text_right(draw, W - PADDING_X, py, tag_txt, self.font_stat_label, (210, 80, 80))
        elif priority:
            self._text_center(draw, W // 2, py, 'Pick first', self.font_label, ACCENT_GREEN)
        else:
            tag_txt = '⏳  Pick a map (opponent is also choosing)'
            self._text_right(draw, W - PADDING_X, py, tag_txt, self.font_stat_label, TEXT_SECONDARY)

        # ── Phase instruction strip ───────────────────────────────────────────
        y_phase = y_player + player_h
        phase_bg = (30, 14, 14) if phase == 'ban' else (14, 22, 44)
        draw.rectangle([(0, y_phase), (W, y_phase + phase_h)], fill=phase_bg)
        if phase == 'ban':
            ins = 'Tap buttons below to toggle ban  ·  confirm when ready (up to 3 bans)'
            self._text_center(draw, W // 2, y_phase + 8, ins, self.font_stat_label, TEXT_SECONDARY)

        # ── Face-up portrait card grid ─────────────────────────────────────────
        y_grid = y_phase + phase_h
        for idx in range(6):
            cell_x, cell_y_rel = self._portrait_cell_xy(idx)
            cx = cell_x
            cy = y_grid + cell_y_rel

            if idx >= len(candidates):
                draw.rounded_rectangle(
                    (cx, cy, cx + self._PC_CELL_W, cy + self._PC_CELL_H),
                    radius=self._PC_RADIUS, fill=(22, 22, 35),
                )
                continue

            m      = candidates[idx]
            bid    = m.get('beatmap_id')
            is_ban = bid in banned_ids
            cover  = covers[idx] if idx < len(covers) else None

            draw = self._draw_portrait_face_up(
                img, cx, cy, m, cover,
                is_banned=is_ban,
                glow_rgb=None,
                card_num=idx + 1,
            )

        # ── Footer ────────────────────────────────────────────────────────────
        y_footer = H - footer_h
        draw.rectangle([(0, y_footer), (W, H)], fill=HEADER_BG)
        if phase == 'ban':
            ft = f'Bans used: {ban_count}/{max_bans}  ·  tap map buttons to toggle  ·  confirm when ready'
        else:
            ft = 'You pick first' if priority else 'Opponent picks, now your turn'
        self._text_center(draw, W // 2, y_footer + 10, ft, self.font_stat_label, TEXT_SECONDARY)

        return self._save(img)

    async def generate_bsk_pool_dm_card_async(self, data: Dict) -> BytesIO:
        """Download map covers then render player DM pool card."""
        candidates = data.get('candidates', [])
        cover_tasks = []
        for m in candidates:
            bsid = m.get('beatmapset_id')
            if bsid:
                url = f"https://assets.ppy.sh/beatmaps/{bsid}/covers/list.jpg"
                cover_tasks.append(download_image(url))
            else:
                cover_tasks.append(_none_coro())
        results = await asyncio.gather(*cover_tasks, return_exceptions=True)
        covers  = [None if isinstance(r, Exception) or r is None else r for r in results]
        data    = {**data, 'covers': covers}
        return await asyncio.to_thread(self.generate_bsk_pool_dm_card, data)

    # ─────────────────────────────────────────────────────────────────────────
    # ROUND START CARD  (VS layout)
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_round_start_card(self, data: Dict) -> BytesIO:
        W = CARD_WIDTH
        header_h = 36
        map_bar_h = 76          # taller to fit cover BG + 3 text rows
        names_h = 44
        bars_h = 4 * 30 + 8
        score_h = 44
        H = header_h + map_bar_h + names_h + bars_h + score_h

        img, draw = self._create_canvas(W, H)
        round_num = data.get('round_number', 1)
        p1_name = data.get('p1_name', 'P1')
        p2_name = data.get('p2_name', 'P2')
        p1_country = data.get('p1_country', '')
        p2_country = data.get('p2_country', '')
        stars = data.get('star_rating', 0.0)
        sr_col = _sr_color(stars)
        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL', f'Round {round_num}', W)

        # ── Map info bar ──────────────────────────────────────────────────────
        y_map = header_h
        map_cover = data.get('map_cover')
        if map_cover:
            try:
                cropped = cover_center_crop(map_cover.convert("RGBA"), W, map_bar_h)
                overlay = Image.new("RGBA", (W, map_bar_h), (0, 0, 0, 185))
                blended = Image.alpha_composite(cropped, overlay)
                img.paste(blended.convert("RGB"), (0, y_map))
                draw = ImageDraw.Draw(img)
            except Exception:
                draw.rectangle([(0, y_map), (W, y_map + map_bar_h)], fill=HEADER_BG)
        else:
            draw.rectangle([(0, y_map), (W, y_map + map_bar_h)], fill=HEADER_BG)

        # Artist — Title  (line 1, centered)
        artist = data.get('beatmap_artist', '')
        bname  = data.get('beatmap_name', '')
        if artist and bname:
            display_title = f'{artist} — {bname}'
        else:
            display_title = data.get('beatmap_title', 'Unknown Map')
        if len(display_title) > 58:
            display_title = display_title[:55] + '…'
        self._text_center(draw, W // 2, y_map + 8, display_title, self.font_label, TEXT_PRIMARY)

        # [version]  (line 2, centered, secondary colour)
        version = data.get('beatmap_version', '')
        if version:
            disp_ver = f'[{version}]'
            if len(disp_ver) > 50:
                disp_ver = disp_ver[:47] + '…]'
            self._text_center(draw, W // 2, y_map + 28, disp_ver, self.font_stat_label, TEXT_SECONDARY)

        # Meta row — with icons (line 3)
        bpm_icon = load_icon('bpm', size=12)
        length_icon = load_icon('timer', size=12)
        bpm    = data.get('bpm')
        length = data.get('length_seconds')
        meta_parts = []
        if length:
            mins, secs = divmod(length, 60)
            meta_parts.append(f'{length_icon} {mins}:{secs:02d}')
        if bpm:
            meta_parts.append(f'{bpm_icon} {bpm:.0f} BPM')
        if meta_parts:
            self._text_center(draw, W // 2, y_map + 46, '  ·  '.join(meta_parts),
                              self.font_stat_label, TEXT_SECONDARY)

        # SR badge — top-right of map bar (same style as pick card)
        star_icon_sm = load_icon('star', size=12)
        sr_str = f'{stars:.2f}'
        sr_bb  = draw.textbbox((0, 0), sr_str, font=self.font_stat_label)
        sr_tw  = sr_bb[2] - sr_bb[0]
        sr_th  = sr_bb[3] - sr_bb[1]
        icon_sz  = star_icon_sm.width if star_icon_sm else 0
        icon_gap = 3 if star_icon_sm else 0
        pad_x, pad_y = 5, 3
        sr_badge_w = pad_x + icon_sz + icon_gap + sr_tw + pad_x
        sr_badge_h = sr_th + pad_y * 2 + 2
        sr_badge_x = W - PADDING_X - sr_badge_w
        sr_badge_y = y_map + 8
        draw.rounded_rectangle(
            (sr_badge_x, sr_badge_y, sr_badge_x + sr_badge_w, sr_badge_y + sr_badge_h),
            radius=4, fill=sr_col,
        )
        icon_y = sr_badge_y + (sr_badge_h - icon_sz) // 2
        if star_icon_sm:
            draw = _paste_icon(img, star_icon_sm, sr_badge_x + pad_x, icon_y)
        draw.text(
            (sr_badge_x + pad_x + icon_sz + icon_gap, sr_badge_y + pad_y - sr_bb[1]),
            sr_str, font=self.font_stat_label, fill=(255, 255, 255),
        )

        # ── Names row with VS icon ────────────────────────────────────────────
        y_names = y_map + map_bar_h
        half = W // 2

        vs_section_h = names_h + bars_h
        p1_tint = Image.new('RGB', (half, vs_section_h), (46, 20, 20))
        p2_tint = Image.new('RGB', (W - half, vs_section_h), (18, 30, 56))
        img.paste(p1_tint, (0, y_names))
        img.paste(p2_tint, (half, y_names))
        draw = ImageDraw.Draw(img)

        vs_icon = load_icon('versus', size=34)
        if vs_icon:
            vx = half - vs_icon.width // 2
            vy = y_names + (names_h - vs_icon.height) // 2
            draw = _paste_icon(img, vs_icon, vx, vy)

        name_y = y_names + (names_h - 22) // 2
        # P1: [flag] name — left-aligned
        draw = _draw_name_with_flag(
            img, draw, PADDING_X, name_y,
            p1_name, p1_country, self.font_row, P1_COLOR,
            align='left', flag_h=18,
        )
        # P2: name [flag] — block right-aligned (name left of flag)
        draw = _draw_name_with_flag(
            img, draw, W - PADDING_X, name_y,
            p2_name, p2_country, self.font_row, P2_COLOR,
            align='right', flag_h=18,
        )

        # ── Skill bars ────────────────────────────────────────────────────────
        y_bars = y_names + names_h
        bar_h_px = 10
        bar_gap = 30
        label_col_w = 80

        for i, comp in enumerate(SKILL_KEYS):
            by = y_bars + 4 + i * bar_gap
            color = SKILL_COLORS[comp]

            mu1 = data.get(f'p1_mu_{comp}', 250.0)
            mu2 = data.get(f'p2_mu_{comp}', 250.0)
            bar_max = 1000.0

            bar1_right = half - label_col_w // 2 - 8
            bar1_left  = PADDING_X + 40
            actual_bar_w = bar1_right - bar1_left
            fill1 = max(6, int(actual_bar_w * min(mu1 / bar_max, 1.0)))

            draw.rounded_rectangle((bar1_left, by, bar1_right, by + bar_h_px),
                                   radius=5, fill=(55, 28, 28))
            draw.rounded_rectangle((bar1_right - fill1, by, bar1_right, by + bar_h_px),
                                   radius=5, fill=color)
            self._text_right(draw, bar1_left - 5, by, f'{mu1:.0f}', self.font_stat_label, TEXT_SECONDARY)

            bar2_left  = half + label_col_w // 2 + 8
            bar2_right = W - PADDING_X - 40
            actual_bar_w2 = bar2_right - bar2_left
            fill2 = max(6, int(actual_bar_w2 * min(mu2 / bar_max, 1.0)))

            draw.rounded_rectangle((bar2_left, by, bar2_right, by + bar_h_px),
                                   radius=5, fill=(18, 28, 55))
            draw.rounded_rectangle((bar2_left, by, bar2_left + fill2, by + bar_h_px),
                                   radius=5, fill=color)
            draw.text((bar2_right + 5, by), f'{mu2:.0f}', font=self.font_stat_label, fill=TEXT_SECONDARY)

            self._text_center(draw, half, by, SKILL_LABELS[comp], self.font_stat_label, TEXT_SECONDARY)

        # ── Score progress bar ────────────────────────────────────────────────
        y_score = y_names + vs_section_h
        draw.rectangle([(0, y_score), (W, y_score + score_h)], fill=HEADER_BG)

        score_p1 = data.get('score_p1', 0)
        score_p2 = data.get('score_p2', 0)
        target   = data.get('target_score', 1_000_000)

        self._text_center(draw, W // 2, y_score + 6,
                          f'{int(score_p1):,}  :  {int(score_p2):,}', self.font_label, TEXT_PRIMARY)

        bar_x  = PADDING_X
        bar_w  = W - 2 * PADDING_X
        bar_th = 8
        bar_ty = y_score + 26
        draw.rounded_rectangle((bar_x, bar_ty, bar_x + bar_w, bar_ty + bar_th),
                                radius=4, fill=(40, 40, 62))
        if target > 0:
            p1_fill = int(bar_w * min(score_p1 / target, 1.0))
            p2_fill = int(bar_w * min(score_p2 / target, 1.0))
            # Prevent bars from overlapping: scale proportionally if needed
            if p1_fill + p2_fill > bar_w:
                total = p1_fill + p2_fill
                p1_fill = int(bar_w * p1_fill / total)
                p2_fill = bar_w - p1_fill
            if p1_fill > 0:
                draw.rounded_rectangle((bar_x, bar_ty, bar_x + p1_fill, bar_ty + bar_th),
                                       radius=4, fill=P1_COLOR)
            if p2_fill > 0:
                draw.rounded_rectangle((bar_x + bar_w - p2_fill, bar_ty, bar_x + bar_w, bar_ty + bar_th),
                                       radius=4, fill=P2_COLOR)
        self._text_center(draw, W // 2, bar_ty + bar_th + 4,
                          f'цель {target:,} pts', self.font_stat_label, (80, 80, 105))

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
        W = CARD_WIDTH
        header_h = 36
        map_bar_h = 44
        result_h = 56
        rows_h = 4 * 36
        score_h = 48
        H = header_h + map_bar_h + result_h + rows_h + score_h

        img, draw = self._create_canvas(W, H)
        round_num = data.get('round_number', 1)
        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL', f'Round {round_num} Result', W)

        p1_name = data.get('p1_name', 'P1')
        p2_name = data.get('p2_name', 'P2')
        p1_country = data.get('p1_country', '')
        p2_country = data.get('p2_country', '')
        winner = data.get('winner', 0)

        y = header_h
        draw.rectangle([(0, y), (W, y + map_bar_h)], fill=HEADER_BG)
        title = data.get('beatmap_title', 'Unknown')
        if len(title) > 58:
            title = title[:55] + '…'
        self._text_center(draw, W // 2, y + 6, title, self.font_small, TEXT_PRIMARY)
        stars = data.get('star_rating', 0.0)
        star_icon = load_icon('star', size=13)
        star_str = f'{stars:.2f}'
        star_col = _sr_color(stars)
        if star_icon:
            si_w = star_icon.width
            total_w = si_w + 4 + draw.textbbox((0, 0), star_str, font=self.font_stat_label)[2]
            sx = W // 2 - total_w // 2
            draw = _paste_icon(img, star_icon, sx, y + 24)
            draw.text((sx + si_w + 4, y + 24), star_str, font=self.font_stat_label, fill=star_col)
        else:
            self._text_center(draw, W // 2, y + 24, f'{stars:.2f}★', self.font_stat_label, star_col)
        y += map_bar_h

        winner_name = (p1_name if winner == 1 else p2_name) if winner else None
        winner_col = P1_COLOR if winner == 1 else (P2_COLOR if winner == 2 else TEXT_SECONDARY)
        winner_country = (p1_country if winner == 1 else p2_country) if winner else ''
        banner_bg = (40, 18, 18) if winner == 1 else ((18, 28, 54) if winner == 2 else HEADER_BG)
        draw.rectangle([(0, y), (W, y + result_h)], fill=banner_bg)
        draw.rectangle([(0, y), (W, y + 3)], fill=winner_col)

        if winner_name:
            self._text_center(draw, W // 2, y + 8, '🏆  WINNER', self.font_stat_label, TEXT_SECONDARY)
            flag_obj = load_flag(winner_country, height=20) if winner_country else None
            flag_w = flag_obj.width + 8 if flag_obj else 0
            name_bb = draw.textbbox((0, 0), winner_name, font=self.font_row)
            name_w = name_bb[2] - name_bb[0]
            block_w = flag_w + name_w
            nx = W // 2 - block_w // 2
            if flag_obj:
                draw = _paste_icon(img, flag_obj, nx, y + 28)
                nx += flag_obj.width + 8
            draw.text((nx, y + 26), winner_name, font=self.font_row, fill=winner_col)
            loser = p2_name if winner == 1 else p1_name
            self._text_center(draw, W // 2, y + 38, f'defeated {loser}', self.font_small, TEXT_SECONDARY)
        else:
            self._text_center(draw, W // 2, y + 20, 'DRAW', self.font_row, TEXT_SECONDARY)
        y += result_h

        half = W // 2
        stat_rows = [
            ('POINTS',  f"{data.get('p1_points', 0):,}",  f"{data.get('p2_points', 0):,}"),
            ('ACC',     f"{data.get('p1_acc', 0.0):.2f}%", f"{data.get('p2_acc', 0.0):.2f}%"),
            ('COMBO',   f"{data.get('p1_combo', 0):,}x",  f"{data.get('p2_combo', 0):,}x"),
            ('MISSES',  str(data.get('p1_misses', 0)),    str(data.get('p2_misses', 0))),
        ]
        row_h = 36
        for i, (label, v1, v2) in enumerate(stat_rows):
            ry = y + i * row_h
            draw.rectangle([(0, ry), (W, ry + row_h)], fill=ROW_EVEN if i % 2 == 0 else ROW_ODD)
            if label == 'MISSES':
                m1 = data.get('p1_misses', 0)
                m2 = data.get('p2_misses', 0)
                p1_col = ACCENT_GREEN if m1 <= m2 else (190, 70, 70)
                p2_col = ACCENT_GREEN if m2 <= m1 else (190, 70, 70)
            else:
                p1_col = ACCENT_GREEN if winner == 1 else TEXT_SECONDARY
                p2_col = ACCENT_GREEN if winner == 2 else TEXT_SECONDARY
            draw.text((PADDING_X, ry + 10), v1, font=self.font_label, fill=p1_col)
            self._text_center(draw, half, ry + 10, label, self.font_stat_label, TEXT_SECONDARY)
            self._text_right(draw, W - PADDING_X, ry + 10, v2, self.font_label, p2_col)
        y += len(stat_rows) * row_h

        draw.rectangle([(0, y), (W, y + score_h)], fill=HEADER_BG)
        score_p1 = data.get('score_p1', 0)
        score_p2 = data.get('score_p2', 0)
        target = data.get('target_score', 1_000_000)
        self._text_center(draw, W // 2, y + 6,
                          f'{int(score_p1):,}  :  {int(score_p2):,}', self.font_row, TEXT_PRIMARY)
        bar_x = PADDING_X
        bar_w = W - 2 * PADDING_X
        bar_th = 8
        bar_ty = y + 28
        draw.rounded_rectangle((bar_x, bar_ty, bar_x + bar_w, bar_ty + bar_th), radius=4, fill=(40, 40, 62))
        if target > 0:
            p1_fill = int(bar_w * min(score_p1 / target, 1.0))
            p2_fill = int(bar_w * min(score_p2 / target, 1.0))
            if p1_fill > 0:
                draw.rounded_rectangle((bar_x, bar_ty, bar_x + p1_fill, bar_ty + bar_th),
                                       radius=4, fill=P1_COLOR)
            if p2_fill > 0:
                draw.rounded_rectangle((bar_x + bar_w - p2_fill, bar_ty, bar_x + bar_w, bar_ty + bar_th),
                                       radius=4, fill=P2_COLOR)
        draw.text((PADDING_X, bar_ty + bar_th + 4), p1_name, font=self.font_stat_label, fill=P1_COLOR)
        self._text_right(draw, W - PADDING_X, bar_ty + bar_th + 4, p2_name,
                         self.font_stat_label, P2_COLOR)
        self._text_center(draw, W // 2, bar_ty + bar_th + 4,
                          f'/ {target:,}', self.font_stat_label, (75, 75, 100))

        return self._save(img)

    async def generate_bsk_round_result_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_bsk_round_result_card, data)

    # ─────────────────────────────────────────────────────────────────────────
    # DUEL END CARD
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_duel_end_card(self, data: Dict) -> BytesIO:
        W = CARD_WIDTH
        header_h = 36
        winner_h = 92
        score_h = 44
        ratings_h = 88
        rounds = data.get('rounds', [])
        round_row_h = 40
        rounds_section_h = (len(rounds) * round_row_h + 16) if rounds else 0
        H = header_h + winner_h + score_h + ratings_h + rounds_section_h

        img, draw = self._create_canvas(W, H)
        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL', 'Final Result', W)

        p1_name = data.get('p1_name', 'P1')
        p2_name = data.get('p2_name', 'P2')
        p1_country = data.get('p1_country', '')
        p2_country = data.get('p2_country', '')
        winner = data.get('winner', 0)
        score_p1 = int(data.get('score_p1', 0))
        score_p2 = int(data.get('score_p2', 0))
        mode = data.get('mode', 'casual').upper()
        total_rounds = data.get('total_rounds', 0)
        is_test = data.get('is_test', False)

        y = header_h
        winner_name = (p1_name if winner == 1 else p2_name) if winner else None
        winner_col = P1_COLOR if winner == 1 else (P2_COLOR if winner == 2 else TEXT_SECONDARY)
        winner_country = (p1_country if winner == 1 else p2_country) if winner else ''
        banner_bg = (40, 16, 16) if winner == 1 else ((16, 26, 54) if winner == 2 else HEADER_BG)
        draw.rectangle([(0, y), (W, y + winner_h)], fill=banner_bg)
        draw.rectangle([(0, y), (W, y + 4)], fill=winner_col)

        mode_str = f'{mode} · {total_rounds} rounds' + (' [ТЕСТ]' if is_test else '')
        self._text_right(draw, W - PADDING_X, y + 8, mode_str, self.font_stat_label, TEXT_SECONDARY)

        if winner_name:
            self._text_center(draw, W // 2, y + 10, '🏆  ПОБЕДИТЕЛЬ', self.font_stat_label, TEXT_SECONDARY)
            flag_obj = load_flag(winner_country, height=22) if winner_country else None
            flag_w = flag_obj.width + 10 if flag_obj else 0
            name_bb = draw.textbbox((0, 0), winner_name, font=self.font_big)
            name_w = name_bb[2] - name_bb[0]
            block_w = flag_w + name_w
            nx = W // 2 - block_w // 2
            if flag_obj:
                draw = _paste_icon(img, flag_obj, nx, y + 32)
                nx += flag_obj.width + 10
            draw.text((nx, y + 30), winner_name, font=self.font_big, fill=winner_col)
            loser = p2_name if winner == 1 else p1_name
            self._text_center(draw, W // 2, y + 68, f'defeated {loser}', self.font_small, TEXT_SECONDARY)
        else:
            self._text_center(draw, W // 2, y + 32, 'НИЧЬЯ', self.font_big, TEXT_SECONDARY)
        y += winner_h

        draw.rectangle([(0, y), (W, y + score_h)], fill=HEADER_BG)
        self._text_center(draw, W // 2, y + 6,
                          f'{score_p1:,}  :  {score_p2:,}', self.font_row, TEXT_PRIMARY)
        bar_x = PADDING_X
        bar_w = W - 2 * PADDING_X
        bar_th = 8
        bar_ty = y + 28
        total_sc = score_p1 + score_p2
        draw.rounded_rectangle((bar_x, bar_ty, bar_x + bar_w, bar_ty + bar_th), radius=4, fill=(40, 40, 62))
        if total_sc > 0:
            ratio = score_p1 / total_sc
            split = int(bar_w * ratio)
            if split > 1:
                draw.rounded_rectangle((bar_x, bar_ty, bar_x + split - 1, bar_ty + bar_th),
                                       radius=4, fill=P1_COLOR)
            if split < bar_w - 1:
                draw.rounded_rectangle((bar_x + split + 1, bar_ty, bar_x + bar_w, bar_ty + bar_th),
                                       radius=4, fill=P2_COLOR)
        draw.text((PADDING_X, bar_ty + bar_th + 3), p1_name, font=self.font_stat_label, fill=P1_COLOR)
        self._text_right(draw, W - PADDING_X, bar_ty + bar_th + 3, p2_name,
                         self.font_stat_label, P2_COLOR)
        y += score_h

        draw.rectangle([(0, y), (W, y + ratings_h)], fill=(20, 20, 34))
        has_deltas = any(data.get(f'p1_delta_{c}') is not None for c in SKILL_KEYS)

        if has_deltas and not is_test:
            panel_gap = 8
            panel_count = 4
            panel_w = (W - 2 * PADDING_X - (panel_count - 1) * panel_gap) // panel_count

            def fmt_delta(d):
                return f'+{d:.1f}' if d >= 0 else f'{d:.1f}'

            for i, comp in enumerate(SKILL_KEYS):
                px = PADDING_X + i * (panel_w + panel_gap)
                py = y + 8
                ph = ratings_h - 16
                color = SKILL_COLORS[comp]
                draw.rounded_rectangle((px, py, px + panel_w, py + ph), radius=6, fill=(28, 28, 48))
                draw.rounded_rectangle((px, py, px + panel_w, py + 3), radius=2, fill=color)
                self._text_center(draw, px + panel_w // 2, py + 6,
                                   SKILL_LABELS[comp], self.font_stat_label, TEXT_SECONDARY)
                d1 = data.get(f'p1_delta_{comp}') or 0
                d2 = data.get(f'p2_delta_{comp}') or 0
                d1_col = ACCENT_GREEN if d1 >= 0 else (190, 70, 70)
                d2_col = ACCENT_GREEN if d2 >= 0 else (190, 70, 70)
                draw.text((px + 6, py + 26), fmt_delta(d1), font=self.font_small, fill=d1_col)
                draw.text((px + 6, py + 46), p1_name[:8], font=self.font_stat_label, fill=P1_COLOR)
                self._text_right(draw, px + panel_w - 6, py + 26, fmt_delta(d2),
                                 self.font_small, d2_col)
                self._text_right(draw, px + panel_w - 6, py + 46, p2_name[:8],
                                 self.font_stat_label, P2_COLOR)
        else:
            msg = 'Рейтинг не изменён (тестовая дуэль)' if is_test else 'Изменения рейтинга недоступны'
            self._text_center(draw, W // 2, y + ratings_h // 2 - 10, msg, self.font_label, TEXT_SECONDARY)
        y += ratings_h

        if rounds:
            draw.line([(PADDING_X, y + 6), (W - PADDING_X, y + 6)], fill=(50, 50, 72), width=1)
            y += 14
            star_icon_sm = load_icon('star', size=11)
            for i, rnd in enumerate(rounds):
                ry = y + i * round_row_h
                draw.rectangle([(0, ry), (W, ry + round_row_h)],
                                fill=ROW_EVEN if i % 2 == 0 else ROW_ODD)
                rnum = rnd.get('round_number', i + 1)
                rtitle = rnd.get('beatmap_title', 'Unknown')
                if len(rtitle) > 42:
                    rtitle = rtitle[:39] + '…'
                rsr = rnd.get('star_rating', 0.0)
                rwinner = rnd.get('winner', 0)
                rp1 = rnd.get('p1_points', 0)
                rp2 = rnd.get('p2_points', 0)

                badge_w = 26
                draw.rounded_rectangle(
                    (PADDING_X, ry + 7, PADDING_X + badge_w, ry + round_row_h - 7),
                    radius=4, fill=ACCENT_RED,
                )
                self._text_center(draw, PADDING_X + badge_w // 2, ry + 10,
                                   str(rnum), self.font_stat_label, TEXT_PRIMARY)

                info_x = PADDING_X + badge_w + 8
                sr_col = _sr_color(rsr)
                sr_str = f'{rsr:.1f}'
                if star_icon_sm:
                    draw = _paste_icon(img, star_icon_sm, info_x, ry + 8)
                    draw.text((info_x + star_icon_sm.width + 3, ry + 7), sr_str,
                              font=self.font_stat_label, fill=sr_col)
                    map_info_x = info_x + star_icon_sm.width + 3 + \
                                 draw.textbbox((0, 0), sr_str, font=self.font_stat_label)[2] + 8
                else:
                    draw.text((info_x, ry + 7), f'{rsr:.1f}★', font=self.font_stat_label, fill=sr_col)
                    map_info_x = info_x + 38

                draw.text((map_info_x, ry + 7), rtitle, font=self.font_stat_label, fill=TEXT_PRIMARY)

                pts_col1 = ACCENT_GREEN if rwinner == 1 else TEXT_SECONDARY
                pts_col2 = ACCENT_GREEN if rwinner == 2 else TEXT_SECONDARY
                p2_pts_str = f'{rp2:,}'
                sep_str = '  :  '
                p1_pts_str = f'{rp1:,}'
                p2_bb = draw.textbbox((0, 0), p2_pts_str, font=self.font_small)
                sep_bb = draw.textbbox((0, 0), sep_str, font=self.font_small)
                p1_bb = draw.textbbox((0, 0), p1_pts_str, font=self.font_small)
                rx = W - PADDING_X
                draw.text((rx - (p2_bb[2]-p2_bb[0]), ry + 13), p2_pts_str,
                          font=self.font_small, fill=pts_col2)
                rx -= (p2_bb[2]-p2_bb[0])
                draw.text((rx - (sep_bb[2]-sep_bb[0]), ry + 13), sep_str,
                          font=self.font_small, fill=TEXT_SECONDARY)
                rx -= (sep_bb[2]-sep_bb[0])
                draw.text((rx - (p1_bb[2]-p1_bb[0]), ry + 13), p1_pts_str,
                          font=self.font_small, fill=pts_col1)

        return self._save(img)

    async def generate_bsk_duel_end_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_bsk_duel_end_card, data)
