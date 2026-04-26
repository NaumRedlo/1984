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
from services.image.utils import load_icon, load_flag

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

# Map type → cell tint bg
MTYPE_BG = {
    'aim':   (44, 22, 22),
    'speed': (20, 28, 50),
    'acc':   (20, 40, 26),
    'cons':  (40, 36, 16),
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
    Draw 'flag + name' (align='left') or 'name + flag' (align='right').
    Returns refreshed draw.
    """
    flag = load_flag(country, height=flag_h) if country else None
    flag_w = flag.width if flag else 0
    gap = 6 if flag else 0

    name_bbox = draw.textbbox((0, 0), name, font=font)
    name_w = name_bbox[2] - name_bbox[0]
    name_h = name_bbox[3] - name_bbox[1]
    total_w = flag_w + gap + name_w
    flag_mid_offset = (name_h - flag_h) // 2  # vertically center flag on text

    if align == 'left':
        fx = x
        tx = x + flag_w + gap
    else:  # right — x is right edge
        tx = x - name_w
        fx = tx - gap - flag_w

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
          round_number  int
          p1_name  str        p2_name  str
          p1_country  str     p2_country  str
          p1_picked  int|None  (beatmap_id)
          p2_picked  int|None
          candidates  list[dict] — max 6:
            beatmap_id, title, artist, version, star_rating, map_type
        """
        W = CARD_WIDTH
        header_h = 36
        status_h = 36
        cell_pad = 8
        grid_cols = 3
        grid_rows = 2
        cell_w = (W - 2 * cell_pad - (grid_cols - 1) * cell_pad) // grid_cols
        cell_h = 136
        grid_h = grid_rows * cell_h + (grid_rows - 1) * cell_pad + cell_pad * 2
        H = header_h + status_h + grid_h

        img, draw = self._create_canvas(W, H)
        round_num = data.get('round_number', 1)
        p1_name = data.get('p1_name', 'P1')
        p2_name = data.get('p2_name', 'P2')
        p1_country = data.get('p1_country', '')
        p2_country = data.get('p2_country', '')
        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL', f'Round {round_num} · Map Pick', W)

        # ── Status bar ────────────────────────────────────────────────────────
        y_status = header_h
        draw.rectangle([(0, y_status), (W, y_status + status_h)], fill=HEADER_BG)

        p1_picked = data.get('p1_picked')
        p2_picked = data.get('p2_picked')
        p1_ready = p1_picked is not None
        p2_ready = p2_picked is not None

        # P1 left: flag + name + status icon
        draw = _draw_name_with_flag(
            img, draw, PADDING_X, y_status + 10,
            p1_name, p1_country, self.font_label,
            ACCENT_GREEN if p1_ready else TEXT_SECONDARY,
            align='left', flag_h=16,
        )
        p1_name_bb = draw.textbbox((0, 0), p1_name, font=self.font_label)
        p1_flag = load_flag(p1_country, height=16)
        p1_text_end = PADDING_X + (p1_flag.width + 6 if p1_flag else 0) + (p1_name_bb[2] - p1_name_bb[0])
        status_icon = '✅' if p1_ready else '⏳'
        draw.text((p1_text_end + 6, y_status + 10), status_icon, font=self.font_label,
                  fill=ACCENT_GREEN if p1_ready else TEXT_SECONDARY)

        # P2 right: status icon + name + flag
        p2_name_bb = draw.textbbox((0, 0), p2_name, font=self.font_label)
        p2_flag = load_flag(p2_country, height=16)
        p2_flag_w = p2_flag.width if p2_flag else 0
        p2_total_w = (p2_name_bb[2] - p2_name_bb[0]) + (p2_flag_w + 6 if p2_flag else 0)
        p2_icon = '✅' if p2_ready else '⏳'
        p2_icon_bb = draw.textbbox((0, 0), p2_icon, font=self.font_label)
        p2_icon_w = p2_icon_bb[2] - p2_icon_bb[0]
        p2_start_x = W - PADDING_X - p2_total_w - p2_icon_w - 6
        draw.text((p2_start_x, y_status + 10), p2_icon, font=self.font_label,
                  fill=ACCENT_GREEN if p2_ready else TEXT_SECONDARY)
        draw = _draw_name_with_flag(
            img, draw, W - PADDING_X, y_status + 10,
            p2_name, p2_country, self.font_label,
            ACCENT_GREEN if p2_ready else TEXT_SECONDARY,
            align='right', flag_h=16,
        )

        # Center divider
        cx = W // 2
        draw.line([(cx, y_status + 6), (cx, y_status + status_h - 6)], fill=(60, 60, 80), width=1)

        # ── Map grid ─────────────────────────────────────────────────────────
        candidates = data.get('candidates', [])
        y_grid_start = header_h + status_h + cell_pad
        star_icon = load_icon('star', size=12)

        for idx in range(6):
            col = idx % grid_cols
            row = idx // grid_cols
            cx_cell = cell_pad + col * (cell_w + cell_pad)
            cy_cell = y_grid_start + row * (cell_h + cell_pad)

            if idx < len(candidates):
                m = candidates[idx]
                mtype = m.get('map_type', '')
            else:
                mtype = ''

            # Type-tinted background
            cell_bg = MTYPE_BG.get(mtype, (28, 28, 44))
            draw.rounded_rectangle(
                (cx_cell, cy_cell, cx_cell + cell_w, cy_cell + cell_h),
                radius=8, fill=cell_bg,
            )

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

            # Map type color accent strip at top
            type_color = SKILL_COLORS.get(mtype)
            if type_color:
                draw.rounded_rectangle(
                    (cx_cell, cy_cell, cx_cell + cell_w, cy_cell + 4),
                    radius=2, fill=type_color,
                )

            # Determine pick state
            p1_chose = (p1_picked == bid)
            p2_chose = (p2_picked == bid)
            both = p1_chose and p2_chose

            border_col = GOLD if both else (P1_COLOR if p1_chose else (P2_COLOR if p2_chose else (50, 50, 72)))
            border_w = 3 if (p1_chose or p2_chose) else 1
            draw.rounded_rectangle(
                (cx_cell, cy_cell, cx_cell + cell_w, cy_cell + cell_h),
                radius=8, outline=border_col, width=border_w,
            )

            # Number badge (top-left)
            badge_r = 12
            draw.ellipse(
                (cx_cell + 7, cy_cell + 8,
                 cx_cell + 7 + badge_r * 2, cy_cell + 8 + badge_r * 2),
                fill=ACCENT_RED,
            )
            self._text_center(draw, cx_cell + 7 + badge_r, cy_cell + 9 + badge_r - 1,
                               str(idx + 1), self.font_stat_label, TEXT_PRIMARY)

            # Star rating with icon (top-right)
            star_str = f'{stars:.1f}'
            star_col = _sr_color(stars)
            star_bb = draw.textbbox((0, 0), star_str, font=self.font_stat_label)
            star_text_w = star_bb[2] - star_bb[0]
            icon_gap = 3
            icon_sz = 12
            block_w = (icon_sz + icon_gap if star_icon else 0) + star_text_w
            sr_x = cx_cell + cell_w - 6 - block_w
            if star_icon:
                draw = _paste_icon(img, star_icon, sr_x, cy_cell + 9)
                draw.text((sr_x + icon_sz + icon_gap, cy_cell + 9), star_str,
                          font=self.font_stat_label, fill=star_col)
            else:
                draw.text((cx_cell + cell_w - 6 - star_text_w, cy_cell + 9), star_str,
                          font=self.font_stat_label, fill=star_col)

            # Title
            max_title = 22
            disp_title = title if len(title) <= max_title else title[:max_title - 1] + '…'
            draw.text((cx_cell + 8, cy_cell + 34), disp_title, font=self.font_small, fill=TEXT_PRIMARY)

            # Artist
            max_artist = 26
            disp_artist = artist if len(artist) <= max_artist else artist[:max_artist - 1] + '…'
            draw.text((cx_cell + 8, cy_cell + 52), disp_artist, font=self.font_stat_label, fill=TEXT_SECONDARY)

            # Version
            max_ver = 26
            disp_ver = f'[{version}]' if version else ''
            if len(disp_ver) > max_ver:
                disp_ver = disp_ver[:max_ver - 1] + '…'
            draw.text((cx_cell + 8, cy_cell + 68), disp_ver, font=self.font_stat_label, fill=(110, 135, 185))

            # Map type label bottom-left
            if mtype and type_color:
                type_lbl = {'aim': 'AIM', 'speed': 'SPEED', 'acc': 'ACC', 'cons': 'CONS'}.get(mtype, '')
                if type_lbl:
                    lbl_bb = draw.textbbox((0, 0), type_lbl, font=self.font_stat_label)
                    lbl_w = lbl_bb[2] - lbl_bb[0]
                    draw.rounded_rectangle(
                        (cx_cell + 7, cy_cell + 88, cx_cell + 7 + lbl_w + 8, cy_cell + 88 + 16),
                        radius=4, fill=type_color,
                    )
                    draw.text((cx_cell + 11, cy_cell + 90), type_lbl,
                              font=self.font_stat_label, fill=(20, 20, 30))

            # Pick indicator stripe at bottom
            if p1_chose or p2_chose:
                stripe_y = cy_cell + cell_h - 26
                stripe_col = GOLD if both else (P1_COLOR if p1_chose else P2_COLOR)
                draw.rounded_rectangle(
                    (cx_cell + 6, stripe_y, cx_cell + cell_w - 6, cy_cell + cell_h - 7),
                    radius=4, fill=stripe_col,
                )
                if both:
                    pick_lbl = '✅ оба выбрали'
                elif p1_chose:
                    pick_lbl = f'✅ {p1_name[:12]}'
                else:
                    pick_lbl = f'✅ {p2_name[:12]}'
                self._text_center(draw, cx_cell + cell_w // 2, stripe_y + 3,
                                  pick_lbl, self.font_stat_label, (18, 18, 28))

        return self._save(img)

    # ─────────────────────────────────────────────────────────────────────────
    # ROUND START CARD  (VS layout)
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_round_start_card(self, data: Dict) -> BytesIO:
        """
        data keys:
          round_number, p1_name, p2_name, p1_country, p2_country
          p1_mu_{aim,speed,acc,cons}   float
          p2_mu_{aim,speed,acc,cons}   float
          star_rating  float
          beatmap_title str
          map_type  str
          bpm  float | None
          length_seconds int | None
          ml_winner  int (1|2) | None
          score_p1, score_p2  int   target_score  int
        """
        W = CARD_WIDTH
        header_h = 36
        map_bar_h = 56      # map info: title + meta + ml prediction
        names_h = 44        # player names row with VS icon
        bars_h = 4 * 30 + 8  # 4 skill bars
        score_h = 44        # score progress bar
        H = header_h + map_bar_h + names_h + bars_h + score_h

        img, draw = self._create_canvas(W, H)
        round_num = data.get('round_number', 1)
        p1_name = data.get('p1_name', 'P1')
        p2_name = data.get('p2_name', 'P2')
        p1_country = data.get('p1_country', '')
        p2_country = data.get('p2_country', '')
        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL', f'Round {round_num}', W)

        # ── Map info bar ──────────────────────────────────────────────────────
        y_map = header_h
        draw.rectangle([(0, y_map), (W, y_map + map_bar_h)], fill=HEADER_BG)

        title = data.get('beatmap_title', 'Unknown Map')
        if len(title) > 60:
            title = title[:57] + '…'
        self._text_center(draw, W // 2, y_map + 7, title, self.font_label, TEXT_PRIMARY)

        # Meta line: SR icon + value, timer icon + length, bpm icon + BPM
        stars = data.get('star_rating', 0.0)
        bpm = data.get('bpm')
        length = data.get('length_seconds')

        star_icon = load_icon('star', size=14)
        timer_icon = load_icon('timer', size=14)
        bpm_icon = load_icon('bpm', size=14)

        meta_y = y_map + 28
        meta_x = PADDING_X
        star_col = _sr_color(stars)

        # Star rating
        if star_icon:
            draw = _paste_icon(img, star_icon, meta_x, meta_y)
            meta_x += star_icon.width + 4
        star_str = f'{stars:.2f}★'
        draw.text((meta_x, meta_y), star_str, font=self.font_small, fill=star_col)
        meta_x += draw.textbbox((0, 0), star_str, font=self.font_small)[2] + 16

        # Length
        if length:
            mins, secs = divmod(length, 60)
            len_str = f'{mins}:{secs:02d}'
            if timer_icon:
                draw = _paste_icon(img, timer_icon, meta_x, meta_y)
                meta_x += timer_icon.width + 4
            draw.text((meta_x, meta_y), len_str, font=self.font_small, fill=TEXT_SECONDARY)
            meta_x += draw.textbbox((0, 0), len_str, font=self.font_small)[2] + 16

        # BPM
        if bpm:
            bpm_str = f'{bpm:.0f}'
            if bpm_icon:
                draw = _paste_icon(img, bpm_icon, meta_x, meta_y)
                meta_x += bpm_icon.width + 4
            draw.text((meta_x, meta_y), bpm_str, font=self.font_small, fill=TEXT_SECONDARY)

        # ML prediction — right-aligned, no percentage
        ml_winner = data.get('ml_winner')
        if ml_winner:
            ml_name = p1_name if ml_winner == 1 else p2_name
            ml_col = P1_COLOR if ml_winner == 1 else P2_COLOR
            ml_text = f'Прогноз: {ml_name}'
            self._text_right(draw, W - PADDING_X, meta_y, ml_text, self.font_stat_label, ml_col)

        # ── Names row with VS icon ────────────────────────────────────────────
        y_names = y_map + map_bar_h
        half = W // 2

        # Tinted halves for this entire section (names + bars)
        vs_section_h = names_h + bars_h
        p1_tint = Image.new('RGB', (half, vs_section_h), (46, 20, 20))
        p2_tint = Image.new('RGB', (W - half, vs_section_h), (18, 30, 56))
        img.paste(p1_tint, (0, y_names))
        img.paste(p2_tint, (half, y_names))
        draw = ImageDraw.Draw(img)

        # VS icon centered in names row
        vs_icon = load_icon('versus', size=34)
        if vs_icon:
            vx = half - vs_icon.width // 2
            vy = y_names + (names_h - vs_icon.height) // 2
            draw = _paste_icon(img, vs_icon, vx, vy)

        # P1 name + flag (left)
        draw = _draw_name_with_flag(
            img, draw, PADDING_X, y_names + (names_h - 22) // 2,
            p1_name, p1_country, self.font_row, P1_COLOR,
            align='left', flag_h=18,
        )
        # P2 name + flag (right)
        draw = _draw_name_with_flag(
            img, draw, W - PADDING_X, y_names + (names_h - 22) // 2,
            p2_name, p2_country, self.font_row, P2_COLOR,
            align='right', flag_h=18,
        )

        # ── Skill bars ────────────────────────────────────────────────────────
        y_bars = y_names + names_h
        bar_h_px = 10
        bar_gap = 30
        # Center column for skill labels: 80px wide
        label_col_w = 80
        bar_area_w = half - PADDING_X - label_col_w // 2 - 8

        for i, comp in enumerate(SKILL_KEYS):
            by = y_bars + 4 + i * bar_gap
            color = SKILL_COLORS[comp]

            mu1 = data.get(f'p1_mu_{comp}', 250.0)
            mu2 = data.get(f'p2_mu_{comp}', 250.0)
            bar_max = 1000.0
            fill1 = max(6, int(bar_area_w * min(mu1 / bar_max, 1.0)))
            fill2 = max(6, int(bar_area_w * min(mu2 / bar_max, 1.0)))

            # P1 bar — grows left from center column edge
            bar1_right = half - label_col_w // 2 - 8
            bar1_left = PADDING_X + 40   # leave room for value label
            actual_bar_w = bar1_right - bar1_left
            fill1 = max(6, int(actual_bar_w * min(mu1 / bar_max, 1.0)))

            draw.rounded_rectangle(
                (bar1_left, by, bar1_right, by + bar_h_px),
                radius=5, fill=(55, 28, 28),
            )
            draw.rounded_rectangle(
                (bar1_right - fill1, by, bar1_right, by + bar_h_px),
                radius=5, fill=color,
            )
            # P1 value left of bar
            val1_str = f'{mu1:.0f}'
            self._text_right(draw, bar1_left - 5, by, val1_str, self.font_stat_label, TEXT_SECONDARY)

            # P2 bar — grows right from center column edge
            bar2_left = half + label_col_w // 2 + 8
            bar2_right = W - PADDING_X - 40   # leave room for value label
            actual_bar_w2 = bar2_right - bar2_left
            fill2 = max(6, int(actual_bar_w2 * min(mu2 / bar_max, 1.0)))

            draw.rounded_rectangle(
                (bar2_left, by, bar2_right, by + bar_h_px),
                radius=5, fill=(18, 28, 55),
            )
            draw.rounded_rectangle(
                (bar2_left, by, bar2_left + fill2, by + bar_h_px),
                radius=5, fill=color,
            )
            # P2 value right of bar
            val2_str = f'{mu2:.0f}'
            draw.text((bar2_right + 5, by), val2_str, font=self.font_stat_label, fill=TEXT_SECONDARY)

            # Skill label in center column
            lbl = SKILL_LABELS[comp]
            self._text_center(draw, half, by, lbl, self.font_stat_label, TEXT_SECONDARY)

        # ── Score progress bar ────────────────────────────────────────────────
        y_score = y_names + vs_section_h
        draw.rectangle([(0, y_score), (W, y_score + score_h)], fill=HEADER_BG)

        score_p1 = data.get('score_p1', 0)
        score_p2 = data.get('score_p2', 0)
        target = data.get('target_score', 1_000_000)

        # Score text — clearly above bar
        score_str = f'{int(score_p1):,}  :  {int(score_p2):,}'
        self._text_center(draw, W // 2, y_score + 6, score_str, self.font_label, TEXT_PRIMARY)

        # Progress bar
        bar_x = PADDING_X
        bar_w = W - 2 * PADDING_X
        bar_th = 8
        bar_ty = y_score + 26
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

        # Target label centered below bar
        self._text_center(draw, W // 2, bar_ty + bar_th + 4,
                          f'цель {target:,} pts', self.font_stat_label, (80, 80, 105))

        return self._save(img)

    # ─────────────────────────────────────────────────────────────────────────
    # ROUND RESULT CARD
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_round_result_card(self, data: Dict) -> BytesIO:
        """
        data keys:
          round_number, p1_name, p2_name, p1_country, p2_country
          winner  int (1|2|0)
          p1_points, p2_points  int
          p1_acc, p2_acc  float
          p1_combo, p2_combo  int
          p1_misses, p2_misses  int
          score_p1, score_p2  int   target_score  int
          beatmap_title  str   star_rating  float
        """
        W = CARD_WIDTH
        header_h = 36
        map_bar_h = 44
        result_h = 56       # winner banner
        rows_h = 4 * 36     # 4 stat rows
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

        # ── Map info ──────────────────────────────────────────────────────────
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

        # ── Winner banner ─────────────────────────────────────────────────────
        winner_name = (p1_name if winner == 1 else p2_name) if winner else None
        winner_col = P1_COLOR if winner == 1 else (P2_COLOR if winner == 2 else TEXT_SECONDARY)
        winner_country = (p1_country if winner == 1 else p2_country) if winner else ''
        banner_bg = (40, 18, 18) if winner == 1 else ((18, 28, 54) if winner == 2 else HEADER_BG)
        draw.rectangle([(0, y), (W, y + result_h)], fill=banner_bg)
        draw.rectangle([(0, y), (W, y + 3)], fill=winner_col)

        if winner_name:
            self._text_center(draw, W // 2, y + 8, '🏆  WINNER', self.font_stat_label, TEXT_SECONDARY)
            # Winner name with flag centered
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

        # ── Stat rows ─────────────────────────────────────────────────────────
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
            row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
            draw.rectangle([(0, ry), (W, ry + row_h)], fill=row_bg)

            # Colour logic
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

        # ── Score progress ─────────────────────────────────────────────────────
        draw.rectangle([(0, y), (W, y + score_h)], fill=HEADER_BG)

        score_p1 = data.get('score_p1', 0)
        score_p2 = data.get('score_p2', 0)
        target = data.get('target_score', 1_000_000)

        # Score text above bar
        self._text_center(draw, W // 2, y + 6, f'{int(score_p1):,}  :  {int(score_p2):,}',
                          self.font_row, TEXT_PRIMARY)

        # Progress bar
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

        # Player name labels below bar
        draw.text((PADDING_X, bar_ty + bar_th + 4), p1_name, font=self.font_stat_label, fill=P1_COLOR)
        self._text_right(draw, W - PADDING_X, bar_ty + bar_th + 4, p2_name, self.font_stat_label, P2_COLOR)
        self._text_center(draw, W // 2, bar_ty + bar_th + 4,
                          f'/ {target:,}', self.font_stat_label, (75, 75, 100))

        return self._save(img)

    # ─────────────────────────────────────────────────────────────────────────
    # DUEL END CARD
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_duel_end_card(self, data: Dict) -> BytesIO:
        """
        data keys:
          p1_name, p2_name, p1_country, p2_country
          winner  int (1|2|0)
          score_p1, score_p2  int
          mode  str   total_rounds  int   is_test  bool
          p1_delta_{aim,speed,acc,cons}  float | None
          p2_delta_{aim,speed,acc,cons}  float | None
          rounds  list[dict]
        """
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

        # ── Winner banner ─────────────────────────────────────────────────────
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
            # Winner name with flag
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

        # ── Score comparison bar ───────────────────────────────────────────────
        draw.rectangle([(0, y), (W, y + score_h)], fill=HEADER_BG)
        score_str = f'{score_p1:,}  :  {score_p2:,}'
        self._text_center(draw, W // 2, y + 6, score_str, self.font_row, TEXT_PRIMARY)

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
        self._text_right(draw, W - PADDING_X, bar_ty + bar_th + 3, p2_name, self.font_stat_label, P2_COLOR)
        y += score_h

        # ── Rating deltas ─────────────────────────────────────────────────────
        draw.rectangle([(0, y), (W, y + ratings_h)], fill=(20, 20, 34))
        has_deltas = any(data.get(f'p1_delta_{c}') is not None for c in SKILL_KEYS)

        if has_deltas and not is_test:
            panel_gap = 8
            panel_count = 4
            panel_w = (W - 2 * PADDING_X - (panel_count - 1) * panel_gap) // panel_count

            for i, comp in enumerate(SKILL_KEYS):
                px = PADDING_X + i * (panel_w + panel_gap)
                py = y + 8
                ph = ratings_h - 16
                color = SKILL_COLORS[comp]

                draw.rounded_rectangle((px, py, px + panel_w, py + ph), radius=6, fill=(28, 28, 48))
                # Color accent top strip
                draw.rounded_rectangle((px, py, px + panel_w, py + 3), radius=2, fill=color)

                lbl = SKILL_LABELS[comp]
                self._text_center(draw, px + panel_w // 2, py + 6, lbl, self.font_stat_label, TEXT_SECONDARY)

                d1 = data.get(f'p1_delta_{comp}') or 0
                d2 = data.get(f'p2_delta_{comp}') or 0

                def fmt_delta(d):
                    return f'+{d:.1f}' if d >= 0 else f'{d:.1f}'

                d1_col = ACCENT_GREEN if d1 >= 0 else (190, 70, 70)
                d2_col = ACCENT_GREEN if d2 >= 0 else (190, 70, 70)

                # P1 delta
                draw.text((px + 6, py + 26), fmt_delta(d1), font=self.font_small, fill=d1_col)
                draw.text((px + 6, py + 46), (p1_name[:8] if len(p1_name) > 8 else p1_name),
                          font=self.font_stat_label, fill=P1_COLOR)
                # P2 delta
                self._text_right(draw, px + panel_w - 6, py + 26, fmt_delta(d2), self.font_small, d2_col)
                self._text_right(draw, px + panel_w - 6, py + 46,
                                 (p2_name[:8] if len(p2_name) > 8 else p2_name),
                                 self.font_stat_label, P2_COLOR)
        else:
            msg = 'Рейтинг не изменён (тестовая дуэль)' if is_test else 'Изменения рейтинга недоступны'
            self._text_center(draw, W // 2, y + ratings_h // 2 - 10, msg, self.font_label, TEXT_SECONDARY)
        y += ratings_h

        # ── Round history ─────────────────────────────────────────────────────
        if rounds:
            draw.line([(PADDING_X, y + 6), (W - PADDING_X, y + 6)], fill=(50, 50, 72), width=1)
            y += 14
            star_icon_sm = load_icon('star', size=11)
            for i, rnd in enumerate(rounds):
                ry = y + i * round_row_h
                row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
                draw.rectangle([(0, ry), (W, ry + round_row_h)], fill=row_bg)

                rnum = rnd.get('round_number', i + 1)
                rtitle = rnd.get('beatmap_title', 'Unknown')
                if len(rtitle) > 42:
                    rtitle = rtitle[:39] + '…'
                rsr = rnd.get('star_rating', 0.0)
                rwinner = rnd.get('winner', 0)
                rp1 = rnd.get('p1_points', 0)
                rp2 = rnd.get('p2_points', 0)

                # Round badge
                badge_w = 26
                draw.rounded_rectangle(
                    (PADDING_X, ry + 7, PADDING_X + badge_w, ry + round_row_h - 7),
                    radius=4, fill=ACCENT_RED,
                )
                self._text_center(draw, PADDING_X + badge_w // 2, ry + 10,
                                   str(rnum), self.font_stat_label, TEXT_PRIMARY)

                info_x = PADDING_X + badge_w + 8

                # SR with icon
                sr_col = _sr_color(rsr)
                sr_str = f'{rsr:.1f}'
                if star_icon_sm:
                    draw = _paste_icon(img, star_icon_sm, info_x, ry + 8)
                    draw.text((info_x + star_icon_sm.width + 3, ry + 7), sr_str,
                              font=self.font_stat_label, fill=sr_col)
                    map_info_x = info_x + star_icon_sm.width + 3 + draw.textbbox((0, 0), sr_str, font=self.font_stat_label)[2] + 8
                else:
                    draw.text((info_x, ry + 7), f'{rsr:.1f}★', font=self.font_stat_label, fill=sr_col)
                    map_info_x = info_x + 38

                draw.text((map_info_x, ry + 7), rtitle, font=self.font_stat_label, fill=TEXT_PRIMARY)

                # Points right-aligned
                pts_col1 = ACCENT_GREEN if rwinner == 1 else TEXT_SECONDARY
                pts_col2 = ACCENT_GREEN if rwinner == 2 else TEXT_SECONDARY
                p1_pts_str = f'{rp1:,}'
                sep_str = '  :  '
                p2_pts_str = f'{rp2:,}'

                # Draw right to left
                p2_bb = draw.textbbox((0, 0), p2_pts_str, font=self.font_small)
                p2_w = p2_bb[2] - p2_bb[0]
                sep_bb = draw.textbbox((0, 0), sep_str, font=self.font_small)
                sep_w = sep_bb[2] - sep_bb[0]
                p1_bb = draw.textbbox((0, 0), p1_pts_str, font=self.font_small)
                p1_w = p1_bb[2] - p1_bb[0]

                rx = W - PADDING_X
                draw.text((rx - p2_w, ry + 13), p2_pts_str, font=self.font_small, fill=pts_col2)
                rx -= p2_w
                draw.text((rx - sep_w, ry + 13), sep_str, font=self.font_small, fill=TEXT_SECONDARY)
                rx -= sep_w
                draw.text((rx - p1_w, ry + 13), p1_pts_str, font=self.font_small, fill=pts_col1)

        return self._save(img)

    # ── Async wrappers ─────────────────────────────────────────────────────────

    async def generate_bsk_pick_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_bsk_pick_card, data)

    async def generate_bsk_round_start_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_bsk_round_start_card, data)

    async def generate_bsk_round_result_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_bsk_round_result_card, data)

    async def generate_bsk_duel_end_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_bsk_duel_end_card, data)
