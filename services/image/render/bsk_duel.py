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
    # POOL GROUP CARD  (group chat — face-down cards during ban/pick phase)
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_pool_group_card(self, data: Dict) -> BytesIO:
        """
        Shown in the group chat during the ban/pick phase.
        Cards are face-down (osu! logo on back); picked cards get a colored border+stripe.

        data keys:
          round_number, p1_name, p2_name, p1_country, p2_country
          phase          str   : 'ban' | 'pick'
          p1_ready       bool  : ban phase — finished banning
          p2_ready       bool  : ban phase — finished banning
          p1_picked      int|None  : beatmap_id P1 picked
          p2_picked      int|None  : beatmap_id P2 picked
          candidates     list[dict]: beatmap_id, map_type
          banned_ids     list[int] : beatmap_ids that have been banned
        """
        W = CARD_WIDTH
        header_h = 36
        status_h = 40
        cell_pad = 8
        COLS, ROWS = 3, 2
        cell_w = (W - (COLS + 1) * cell_pad) // COLS
        cell_h = 148
        grid_h = ROWS * cell_h + (ROWS + 1) * cell_pad
        footer_h = 30
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

        phase_label = 'Фаза банов' if phase == 'ban' else 'Фаза пиков'
        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL',
                          f'Round {round_num} · {phase_label}', W)

        # ── Status bar ────────────────────────────────────────────────────────
        y_status = header_h
        draw.rectangle([(0, y_status), (W, y_status + status_h)], fill=HEADER_BG)

        if phase == 'ban':
            p1_ready_col = ACCENT_GREEN if p1_ready else (190, 80, 80)
            p2_ready_col = ACCENT_GREEN if p2_ready else (190, 80, 80)
            p1_sub = '✓ готов' if p1_ready else 'банит...'
            p2_sub = '✓ готов' if p2_ready else 'банит...'
        else:
            p1_ready_col = ACCENT_GREEN if p1_picked else (190, 80, 80)
            p2_ready_col = ACCENT_GREEN if p2_picked else (190, 80, 80)
            p1_sub = '✓ выбрал' if p1_picked else 'думает...'
            p2_sub = '✓ выбрал' if p2_picked else 'думает...'

        name_y = y_status + (status_h - 16) // 2
        draw = _draw_name_with_flag(img, draw, PADDING_X, name_y,
                                    p1_name, p1_country, self.font_label,
                                    p1_ready_col, align='left', flag_h=16)
        draw = _draw_name_with_flag(img, draw, W - PADDING_X, name_y,
                                    p2_name, p2_country, self.font_label,
                                    p2_ready_col, align='right', flag_h=16)

        center_txt = f'{p1_sub}   |   {p2_sub}'
        self._text_center(draw, W // 2, name_y, center_txt, self.font_stat_label, TEXT_SECONDARY)

        # ── Face-down grid ────────────────────────────────────────────────────
        y_grid = header_h + status_h + cell_pad
        osu_icon = load_icon('osu_logo', size=44)

        for idx in range(6):
            col = idx % COLS
            row = idx // COLS
            cx = cell_pad + col * (cell_w + cell_pad)
            cy = y_grid + row * (cell_h + cell_pad)

            if idx >= len(candidates):
                draw.rounded_rectangle((cx, cy, cx + cell_w, cy + cell_h),
                                       radius=10, fill=(22, 22, 35))
                continue

            m       = candidates[idx]
            bid     = m.get('beatmap_id')
            mtype   = m.get('map_type', '')
            is_ban  = bid in banned_ids
            p1_chose = (p1_picked == bid)
            p2_chose = (p2_picked == bid)
            both    = p1_chose and p2_chose

            # Card back
            card_bg = (28, 16, 16) if is_ban else (22, 22, 35)
            draw.rounded_rectangle((cx, cy, cx + cell_w, cy + cell_h),
                                   radius=10, fill=card_bg)

            # Inner decorative border rings
            if not is_ban:
                draw.rounded_rectangle((cx + 8, cy + 8, cx + cell_w - 8, cy + cell_h - 8),
                                       radius=7, outline=(38, 38, 58), width=1)
                draw.rounded_rectangle((cx + 15, cy + 15, cx + cell_w - 15, cy + cell_h - 15),
                                       radius=5, outline=(32, 32, 50), width=1)

            # osu! logo watermark centered on card back
            if osu_icon and not is_ban:
                lx = cx + (cell_w - osu_icon.width) // 2
                ly = cy + (cell_h - osu_icon.height) // 2
                if osu_icon.mode == 'RGBA':
                    r_ch, g_ch, b_ch, a_ch = osu_icon.split()
                    a_dim = a_ch.point(lambda v: v * 55 // 255)
                    osu_dim = Image.merge('RGBA', (r_ch, g_ch, b_ch, a_dim))
                    img.paste(osu_dim, (lx, ly), osu_dim)
                    draw = ImageDraw.Draw(img)

            # Type accent strip at top
            type_color = SKILL_COLORS.get(mtype)
            if type_color and not is_ban:
                draw.rounded_rectangle((cx, cy, cx + cell_w, cy + 4),
                                       radius=2, fill=type_color)

            # Pick/banned border + stripe
            if is_ban:
                draw.rounded_rectangle((cx, cy, cx + cell_w, cy + cell_h),
                                       radius=10, outline=(160, 40, 40), width=2)
                ban_ov = Image.new('RGBA', (cell_w, cell_h), (120, 20, 20, 140))
                img.paste(ban_ov.convert('RGB'), (cx, cy), ban_ov)
                draw = ImageDraw.Draw(img)
                bt = 'БАН'
                bt_bb = draw.textbbox((0, 0), bt, font=self.font_label)
                draw.text((cx + (cell_w - (bt_bb[2]-bt_bb[0])) // 2 - bt_bb[0],
                           cy + (cell_h - (bt_bb[3]-bt_bb[1])) // 2 - bt_bb[1]),
                          bt, font=self.font_label, fill=(220, 80, 80))
                stripe_y = None
            elif both:
                draw.rounded_rectangle((cx, cy, cx + cell_w, cy + cell_h),
                                       radius=10, outline=GOLD, width=3)
                stripe_y = cy + cell_h - 26
                draw.rounded_rectangle((cx + 6, stripe_y, cx + cell_w - 6, cy + cell_h - 6),
                                       radius=4, fill=GOLD)
                self._text_center(draw, cx + cell_w // 2,
                                  stripe_y + 4, 'оба выбрали',
                                  self.font_stat_label, (18, 18, 28))
            elif p1_chose:
                draw.rounded_rectangle((cx, cy, cx + cell_w, cy + cell_h),
                                       radius=10, outline=P1_COLOR, width=3)
                stripe_y = cy + cell_h - 26
                draw.rounded_rectangle((cx + 6, stripe_y, cx + cell_w - 6, cy + cell_h - 6),
                                       radius=4, fill=P1_COLOR)
                self._text_center(draw, cx + cell_w // 2,
                                  stripe_y + 4, p1_name[:16],
                                  self.font_stat_label, (255, 255, 255))
            elif p2_chose:
                draw.rounded_rectangle((cx, cy, cx + cell_w, cy + cell_h),
                                       radius=10, outline=P2_COLOR, width=3)
                stripe_y = cy + cell_h - 26
                draw.rounded_rectangle((cx + 6, stripe_y, cx + cell_w - 6, cy + cell_h - 6),
                                       radius=4, fill=P2_COLOR)
                self._text_center(draw, cx + cell_w // 2,
                                  stripe_y + 4, p2_name[:16],
                                  self.font_stat_label, (255, 255, 255))
            else:
                draw.rounded_rectangle((cx, cy, cx + cell_w, cy + cell_h),
                                       radius=10, outline=(55, 55, 75), width=1)
                stripe_y = None

            # Number circle (bottom-right, raised above stripe if present)
            num_r = 11
            num_cx_c = cx + cell_w - 7 - num_r
            num_cy_c = (cy + cell_h - 7 - num_r) if stripe_y is None else (stripe_y - num_r - 4)
            circ_fill = (70, 25, 25) if is_ban else (50, 50, 72)
            circ_out  = (160, 50, 50) if is_ban else (90, 90, 120)
            draw.ellipse((num_cx_c - num_r, num_cy_c - num_r,
                          num_cx_c + num_r, num_cy_c + num_r),
                         fill=circ_fill, outline=circ_out, width=1)
            ns = str(idx + 1)
            nb = draw.textbbox((0, 0), ns, font=self.font_stat_label)
            draw.text((num_cx_c - (nb[2]-nb[0])//2 - nb[0],
                       num_cy_c - (nb[3]-nb[1])//2 - nb[1]),
                      ns, font=self.font_stat_label,
                      fill=(210, 120, 120) if is_ban else (255, 255, 255))

        # ── Footer ─────────────────────────────────────────────────────────────
        y_footer = H - footer_h
        draw.rectangle([(0, y_footer), (W, H)], fill=HEADER_BG)
        if phase == 'ban':
            if p1_ready and p2_ready:
                ft = 'Оба завершили фазу банов — начинаем пики!'
            else:
                ft = 'Игроки решают, какие карты заблокировать (в личных сообщениях)'
        elif p1_picked and p2_picked:
            ft = 'Оба выбрали карту — определяем раунд!'
        elif p1_picked or p2_picked:
            ft = 'Один игрок выбрал — ждём второго...'
        else:
            ft = 'Игроки выбирают карту (в личных сообщениях)'
        self._text_center(draw, W // 2, y_footer + 8, ft, self.font_stat_label, TEXT_SECONDARY)

        return self._save(img)

    async def generate_bsk_pool_group_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_bsk_pool_group_card, data)

    # ─────────────────────────────────────────────────────────────────────────
    # POOL DM CARD  (player's private view — detailed maps, ban/pick actions)
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_pool_dm_card(self, data: Dict) -> BytesIO:
        """
        Sent privately to each player during the ban/pick phase.
        Shows all 6 maps face-up with full stats; banned cards show a red overlay.

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
        W = CARD_WIDTH
        header_h  = 36
        player_h  = 40   # player name + phase tag
        phase_h   = 32   # instruction strip
        cell_pad  = 8
        COLS, ROWS = 3, 2
        cell_w = (W - (COLS + 1) * cell_pad) // COLS
        cell_h = 196     # taller than group card — room for stats
        grid_h = ROWS * cell_h + (ROWS + 1) * cell_pad
        footer_h  = 34
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

        phase_label = 'Фаза банов' if phase == 'ban' else 'Фаза пиков'
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
            tag_txt = f'🚫  Бан {ban_count}/{max_bans} — выбери карты для бана'
            tag_col = (210, 80, 80)
        elif priority:
            tag_txt = '🎯  Твой ход — выбери карту для пика'
            tag_col = ACCENT_GREEN
        else:
            tag_txt = '⏳  Выбери карту (соперник тоже выбирает)'
            tag_col = TEXT_SECONDARY
        self._text_right(draw, W - PADDING_X, py, tag_txt, self.font_stat_label, tag_col)

        # ── Phase instruction strip ───────────────────────────────────────────
        y_phase = y_player + player_h
        phase_bg = (30, 14, 14) if phase == 'ban' else (14, 22, 44)
        draw.rectangle([(0, y_phase), (W, y_phase + phase_h)], fill=phase_bg)
        if phase == 'ban':
            ins = '/bskban N  →  пометить карту · /bskready  →  завершить (до 3 банов, можно пропустить)'
        else:
            ins = '/bskpick N  →  выбрать карту по её номеру в пуле'
        self._text_center(draw, W // 2, y_phase + 8, ins, self.font_stat_label, TEXT_SECONDARY)

        # ── Map grid ──────────────────────────────────────────────────────────
        y_grid = y_phase + phase_h + cell_pad
        star_icon = load_icon('star', size=12)

        for idx in range(6):
            col = idx % COLS
            row = idx // COLS
            cx = cell_pad + col * (cell_w + cell_pad)
            cy = y_grid + row * (cell_h + cell_pad)

            if idx >= len(candidates):
                draw.rounded_rectangle((cx, cy, cx + cell_w, cy + cell_h),
                                       radius=10, fill=(22, 22, 35))
                continue

            m        = candidates[idx]
            bid      = m.get('beatmap_id')
            title    = m.get('title', 'Unknown')
            artist   = m.get('artist', '')
            version  = m.get('version', '')
            stars    = m.get('star_rating', 0.0)
            mtype    = m.get('map_type', '')
            ar       = m.get('ar')
            od       = m.get('od')
            cs       = m.get('cs')
            hp_val   = m.get('hp')
            bpm      = m.get('bpm')
            drain    = m.get('drain_time')
            is_ban   = bid in banned_ids

            type_color = SKILL_COLORS.get(mtype)
            cell_bg    = MTYPE_BG.get(mtype, (28, 28, 44))
            sr_col     = _sr_color(stars)

            draw.rounded_rectangle((cx, cy, cx + cell_w, cy + cell_h),
                                   radius=10, fill=cell_bg)

            # Cover background (only if not banned)
            cover_img = covers[idx] if idx < len(covers) else None
            if cover_img and not is_ban:
                try:
                    cropped = cover_center_crop(cover_img.convert('RGBA'), cell_w, cell_h)
                    overlay = Image.new('RGBA', (cell_w, cell_h), (0, 0, 0, 175))
                    blended = Image.alpha_composite(cropped, overlay)
                    tint    = Image.new('RGBA', (cell_w, cell_h), (*cell_bg, 70))
                    blended = Image.alpha_composite(blended, tint)
                    img.paste(blended.convert('RGB'), (cx, cy))
                    draw = ImageDraw.Draw(img)
                except Exception:
                    pass

            # Type accent strip (top)
            if type_color:
                draw.rounded_rectangle((cx, cy, cx + cell_w, cy + 4),
                                       radius=2, fill=type_color)

            # Border
            border_col = (55, 55, 75)
            draw.rounded_rectangle((cx, cy, cx + cell_w, cy + cell_h),
                                   radius=10, outline=border_col, width=1)

            # ── Text content (hidden under ban overlay later) ─────────────────
            # Title
            disp_title = title if len(title) <= 22 else title[:21] + '…'
            draw.text((cx + 8, cy + 10), disp_title, font=self.font_label, fill=TEXT_PRIMARY)

            # Artist
            disp_artist = artist if len(artist) <= 28 else artist[:27] + '…'
            draw.text((cx + 8, cy + 29), disp_artist, font=self.font_small, fill=TEXT_SECONDARY)

            # [version]
            disp_ver = f'[{version}]' if version else ''
            if len(disp_ver) > 30:
                disp_ver = disp_ver[:29] + '…'
            draw.text((cx + 8, cy + 47), disp_ver, font=self.font_stat_label, fill=(110, 135, 185))

            # Divider
            draw.line([(cx + 8, cy + 66), (cx + cell_w - 8, cy + 66)],
                      fill=(50, 50, 72), width=1)

            # AR / OD / CS / HP stats
            stats_parts = []
            if ar       is not None: stats_parts.append(f'AR {ar:.1f}')
            if od       is not None: stats_parts.append(f'OD {od:.1f}')
            if cs       is not None: stats_parts.append(f'CS {cs:.1f}')
            if hp_val   is not None: stats_parts.append(f'HP {hp_val:.1f}')
            if stats_parts:
                draw.text((cx + 8, cy + 72), '  ·  '.join(stats_parts),
                          font=self.font_stat_label, fill=(120, 160, 200))

            # BPM + drain time
            meta_parts = []
            if bpm:
                meta_parts.append(f'{bpm:.0f} BPM')
            if drain:
                mm, ss = divmod(drain, 60)
                meta_parts.append(f'{mm}:{ss:02d}')
            if meta_parts:
                draw.text((cx + 8, cy + 89), '  ·  '.join(meta_parts),
                          font=self.font_stat_label, fill=TEXT_SECONDARY)

            # Map type badge (bottom-left area)
            if type_color:
                type_lbl = MTYPE_FULL.get(mtype, '')
                if type_lbl:
                    lbl_bb = draw.textbbox((0, 0), type_lbl, font=self.font_stat_label)
                    lbl_w  = lbl_bb[2] - lbl_bb[0]
                    bx     = cx + 7
                    by_b   = cy + 110
                    draw.rounded_rectangle((bx, by_b, bx + lbl_w + 10, by_b + 17),
                                           radius=4, fill=type_color)
                    draw.text((bx + 5, by_b + 1), type_lbl,
                              font=self.font_stat_label, fill=(18, 18, 28))

            # SR badge (top-right)
            sr_str = f'{stars:.2f}'
            sr_bb  = draw.textbbox((0, 0), sr_str, font=self.font_stat_label)
            sr_tw  = sr_bb[2] - sr_bb[0]
            sr_th  = sr_bb[3] - sr_bb[1]
            icon_sz  = star_icon.width if star_icon else 0
            icon_gap = 3 if star_icon else 0
            pad_x, pad_y = 5, 3
            bw = pad_x + icon_sz + icon_gap + sr_tw + pad_x
            bh = sr_th + pad_y * 2 + 2
            bx = cx + cell_w - 7 - bw
            by = cy + 7
            draw.rounded_rectangle((bx, by, bx + bw, by + bh), radius=4, fill=sr_col)
            iy = by + (bh - icon_sz) // 2
            if star_icon:
                draw = _paste_icon(img, star_icon, bx + pad_x, iy)
            draw.text((bx + pad_x + icon_sz + icon_gap, by + pad_y - sr_bb[1]),
                      sr_str, font=self.font_stat_label, fill=(255, 255, 255))

            # Number circle (bottom-right)
            num_r  = 11
            num_cx_c = cx + cell_w - 7 - num_r
            num_cy_c = cy + cell_h - 7 - num_r
            draw.ellipse((num_cx_c - num_r, num_cy_c - num_r,
                          num_cx_c + num_r, num_cy_c + num_r),
                         fill=(50, 50, 72), outline=(90, 90, 120), width=1)
            ns = str(idx + 1)
            nb = draw.textbbox((0, 0), ns, font=self.font_stat_label)
            draw.text((num_cx_c - (nb[2]-nb[0])//2 - nb[0],
                       num_cy_c - (nb[3]-nb[1])//2 - nb[1]),
                      ns, font=self.font_stat_label, fill=(255, 255, 255))

            # ── Ban overlay (drawn last so it sits on top) ────────────────────
            if is_ban:
                ban_ov = Image.new('RGBA', (cell_w, cell_h), (130, 20, 20, 185))
                img.paste(ban_ov.convert('RGB'), (cx, cy), ban_ov)
                draw = ImageDraw.Draw(img)
                draw.rounded_rectangle((cx, cy, cx + cell_w, cy + cell_h),
                                       radius=10, outline=(210, 55, 55), width=2)
                bt    = 'БАН'
                bt_bb = draw.textbbox((0, 0), bt, font=self.font_row)
                bt_w  = bt_bb[2] - bt_bb[0]
                bt_h  = bt_bb[3] - bt_bb[1]
                draw.text((cx + (cell_w - bt_w) // 2 - bt_bb[0],
                           cy + (cell_h - bt_h) // 2 - bt_bb[1]),
                          bt, font=self.font_row, fill=(255, 255, 255))
                # number circle re-drawn on top of overlay
                draw.ellipse((num_cx_c - num_r, num_cy_c - num_r,
                              num_cx_c + num_r, num_cy_c + num_r),
                             fill=(80, 20, 20), outline=(210, 55, 55), width=1)
                draw.text((num_cx_c - (nb[2]-nb[0])//2 - nb[0],
                           num_cy_c - (nb[3]-nb[1])//2 - nb[1]),
                          ns, font=self.font_stat_label, fill=(230, 140, 140))

        # ── Footer ────────────────────────────────────────────────────────────
        y_footer = H - footer_h
        draw.rectangle([(0, y_footer), (W, H)], fill=HEADER_BG)
        if phase == 'ban':
            ft = (f'Использовано банов: {ban_count}/{max_bans}  ·  '
                  f'/bskban N — пометить  ·  /bskready — завершить')
        else:
            prio_txt = 'Ты выбираешь первым' if priority else 'Соперник выбрал, теперь твой ход'
            ft = f'{prio_txt}  ·  /bskpick N — выбрать карту по номеру'
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
