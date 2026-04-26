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


class BskDuelCardMixin:

    # ─────────────────────────────────────────────────────────────────────────
    # PICK CARD  (3×2 grid, updates per pick)
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_pick_card(self, data: Dict) -> BytesIO:
        """
        data keys:
          round_number  int
          p1_name       str
          p2_name       str
          p1_picked     int | None  (beatmap_id chosen by P1)
          p2_picked     int | None
          candidates    list[dict]  max 6 items
            each: beatmap_id, title, artist, version, star_rating, map_type
        """
        W = CARD_WIDTH
        header_h = 36
        status_h = 38           # thin bar: "P1 ⏳  |  P2 ✅"
        grid_cols = 3
        grid_rows = 2
        cell_pad = 8
        cell_w = (W - 2 * cell_pad - (grid_cols - 1) * cell_pad) // grid_cols
        cell_h = 130
        grid_h = grid_rows * cell_h + (grid_rows - 1) * cell_pad + cell_pad * 2
        footer_h = 34
        H = header_h + status_h + grid_h + footer_h

        img, draw = self._create_canvas(W, H)
        round_num = data.get('round_number', 1)
        p1_name = data.get('p1_name', 'P1')
        p2_name = data.get('p2_name', 'P2')
        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL', f'Round {round_num} · Map Pick', W)

        # ── Status bar ────────────────────────────────────────────────────────
        y_status = header_h
        draw.rectangle([(0, y_status), (W, y_status + status_h)], fill=HEADER_BG)

        p1_picked = data.get('p1_picked')
        p2_picked = data.get('p2_picked')
        p1_icon = '✅' if p1_picked is not None else '⏳'
        p2_icon = '✅' if p2_picked is not None else '⏳'

        p1_col = ACCENT_GREEN if p1_picked is not None else TEXT_SECONDARY
        p2_col = ACCENT_GREEN if p2_picked is not None else TEXT_SECONDARY

        # Left half: P1
        p1_text = f'{p1_icon} {p1_name}'
        draw.text((PADDING_X, y_status + 10), p1_text, font=self.font_label, fill=p1_col)

        # Right half: P2 (right-aligned)
        p2_text = f'{p2_name} {p2_icon}'
        self._text_right(draw, W - PADDING_X, y_status + 10, p2_text, self.font_label, p2_col)

        # Center divider
        cx = W // 2
        draw.line([(cx, y_status + 6), (cx, y_status + status_h - 6)], fill=(60, 60, 80), width=1)

        # ── Map grid ─────────────────────────────────────────────────────────
        candidates = data.get('candidates', [])
        y_grid_start = header_h + status_h + cell_pad

        map_type_icons = {
            'aim':   '🎯',
            'speed': '⚡',
            'acc':   '🎹',
            'cons':  '🔄',
        }

        for idx in range(6):
            col = idx % grid_cols
            row = idx // grid_cols
            cx_cell = cell_pad + col * (cell_w + cell_pad)
            cy_cell = y_grid_start + row * (cell_h + cell_pad)

            # Background
            cell_bg = (28, 28, 44)
            draw.rounded_rectangle(
                (cx_cell, cy_cell, cx_cell + cell_w, cy_cell + cell_h),
                radius=8, fill=cell_bg,
            )

            if idx >= len(candidates):
                # Empty slot
                self._text_center(draw, cx_cell + cell_w // 2, cy_cell + cell_h // 2 - 8,
                                   '—', self.font_label, (60, 60, 80))
                continue

            m = candidates[idx]
            bid = m.get('beatmap_id')
            title = m.get('title', 'Unknown')
            artist = m.get('artist', '')
            version = m.get('version', '')
            stars = m.get('star_rating', 0.0)
            mtype = m.get('map_type', '')

            # Determine pick state
            p1_chose = (p1_picked == bid)
            p2_chose = (p2_picked == bid)
            both = p1_chose and p2_chose

            # Border colour based on who picked
            if both:
                border_col = GOLD
                border_w = 3
            elif p1_chose:
                border_col = P1_COLOR
                border_w = 3
            elif p2_chose:
                border_col = P2_COLOR
                border_w = 3
            else:
                border_col = (50, 50, 70)
                border_w = 1

            draw.rounded_rectangle(
                (cx_cell, cy_cell, cx_cell + cell_w, cy_cell + cell_h),
                radius=8, outline=border_col, width=border_w,
            )

            # Number badge (top-left)
            badge_r = 13
            draw.ellipse(
                (cx_cell + 8, cy_cell + 8, cx_cell + 8 + badge_r * 2, cy_cell + 8 + badge_r * 2),
                fill=ACCENT_RED,
            )
            self._text_center(draw, cx_cell + 8 + badge_r, cy_cell + 8 + badge_r - 2,
                               str(idx + 1), self.font_stat_label, TEXT_PRIMARY)

            # Star rating (top-right)
            star_str = f'{stars:.1f}★'
            star_col = self._sr_color(stars)
            self._text_right(draw, cx_cell + cell_w - 6, cy_cell + 10,
                              star_str, self.font_stat_label, star_col)

            # Map type icon (top-right, below stars)
            mtype_icon = map_type_icons.get(mtype, '🎵')
            self._text_right(draw, cx_cell + cell_w - 6, cy_cell + 26,
                              mtype_icon, self.font_stat_label, TEXT_SECONDARY)

            # Title
            max_title_chars = 22
            disp_title = title if len(title) <= max_title_chars else title[:max_title_chars - 1] + '…'
            draw.text((cx_cell + 8, cy_cell + 38), disp_title, font=self.font_small, fill=TEXT_PRIMARY)

            # Artist
            max_artist_chars = 24
            disp_artist = artist if len(artist) <= max_artist_chars else artist[:max_artist_chars - 1] + '…'
            draw.text((cx_cell + 8, cy_cell + 56), disp_artist, font=self.font_stat_label, fill=TEXT_SECONDARY)

            # Difficulty version
            max_ver_chars = 24
            disp_ver = f'[{version}]' if version else ''
            if len(disp_ver) > max_ver_chars:
                disp_ver = disp_ver[:max_ver_chars - 1] + '…'
            draw.text((cx_cell + 8, cy_cell + 72), disp_ver, font=self.font_stat_label, fill=(120, 140, 180))

            # Pick indicator stripe at bottom
            if p1_chose or p2_chose or both:
                stripe_y = cy_cell + cell_h - 26
                draw.rounded_rectangle(
                    (cx_cell + 6, stripe_y, cx_cell + cell_w - 6, cy_cell + cell_h - 8),
                    radius=4,
                    fill=GOLD if both else (P1_COLOR if p1_chose else P2_COLOR),
                )
                if both:
                    pick_label = '✅ оба выбрали'
                elif p1_chose:
                    pick_label = f'✅ {p1_name}'
                else:
                    pick_label = f'✅ {p2_name}'
                self._text_center(draw, cx_cell + cell_w // 2, stripe_y + 3,
                                  pick_label, self.font_stat_label, (20, 20, 30))

        # Footer
        footer_y = H - footer_h
        self._draw_footer(draw, img, 'BIG BROTHER IS WATCHING YOUR RANK', footer_y, W)
        return self._save(img)

    @staticmethod
    def _sr_color(stars: float):
        if stars < 2.5:
            return (100, 180, 100)
        elif stars < 4.0:
            return (255, 220, 60)
        elif stars < 5.5:
            return (255, 140, 50)
        elif stars < 7.0:
            return (220, 60, 60)
        else:
            return (200, 80, 220)

    # ─────────────────────────────────────────────────────────────────────────
    # ROUND START CARD  (VS layout)
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_round_start_card(self, data: Dict) -> BytesIO:
        """
        data keys:
          round_number, p1_name, p2_name
          p1_mu_{aim,speed,acc,cons}   float
          p2_mu_{aim,speed,acc,cons}   float
          star_rating  float
          beatmap_title str  (artist - title [ver])
          map_type  str
          bpm  float | None
          length_seconds int | None
          ml_winner  int (1 or 2) | None
          ml_conf    float | None
          score_p1   int   (current total)
          score_p2   int
          target_score int
          osu_url  str | None
        """
        W = CARD_WIDTH
        header_h = 36
        hero_h = 58       # map info
        vs_h = 180        # skill bars section
        score_bar_h = 28
        footer_h = 34
        H = header_h + hero_h + vs_h + score_bar_h + footer_h

        img, draw = self._create_canvas(W, H)

        round_num = data.get('round_number', 1)
        p1_name = data.get('p1_name', 'P1')
        p2_name = data.get('p2_name', 'P2')
        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL', f'Round {round_num}', W)

        # ── Map hero bar ──────────────────────────────────────────────────────
        y_hero = header_h
        draw.rectangle([(0, y_hero), (W, y_hero + hero_h)], fill=HEADER_BG)

        title = data.get('beatmap_title', 'Unknown Map')
        if len(title) > 58:
            title = title[:55] + '…'
        self._text_center(draw, W // 2, y_hero + 8, title, self.font_label, TEXT_PRIMARY)

        stars = data.get('star_rating', 0.0)
        star_str = f'{stars:.2f}★'
        star_col = self._sr_color(stars)

        bpm = data.get('bpm')
        length = data.get('length_seconds')
        bpm_str = f'{bpm:.0f} BPM' if bpm else ''
        if length:
            mins, secs = divmod(length, 60)
            len_str = f'{mins}:{secs:02d}'
        else:
            len_str = ''

        meta_parts = [star_str]
        if len_str:
            meta_parts.append(len_str)
        if bpm_str:
            meta_parts.append(bpm_str)
        meta_str = '  ·  '.join(meta_parts)
        self._text_center(draw, W // 2, y_hero + 32, meta_str, self.font_small, star_col)

        # ML prediction badge
        ml_winner = data.get('ml_winner')
        ml_conf = data.get('ml_conf')
        if ml_winner and ml_conf is not None:
            ml_name = p1_name if ml_winner == 1 else p2_name
            ml_text = f'🤖 {ml_name} ({ml_conf*100:.0f}%)'
            ml_col = P1_COLOR if ml_winner == 1 else P2_COLOR
            self._text_right(draw, W - PADDING_X, y_hero + 10, ml_text, self.font_stat_label, ml_col)

        # ── VS section ────────────────────────────────────────────────────────
        y_vs = y_hero + hero_h
        half = W // 2

        # Tinted halves
        p1_tint = Image.new('RGB', (half, vs_h), (50, 22, 22))
        p2_tint = Image.new('RGB', (W - half, vs_h), (22, 34, 58))
        img.paste(p1_tint, (0, y_vs))
        img.paste(p2_tint, (half, y_vs))
        draw = ImageDraw.Draw(img)

        # Player names
        draw.text((PADDING_X, y_vs + 10), p1_name, font=self.font_row, fill=P1_COLOR)
        self._text_right(draw, W - PADDING_X, y_vs + 10, p2_name, self.font_row, P2_COLOR)

        # Skill bars — mirrored
        bar_h_px = 12
        bar_gap = 26
        bar_area_w = half - PADDING_X - 50   # leave 50px for center diamond
        bars_y_start = y_vs + 38
        bar_max = 1000.0

        for i, comp in enumerate(SKILL_KEYS):
            by = bars_y_start + i * bar_gap
            color = SKILL_COLORS[comp]

            mu1 = data.get(f'p1_mu_{comp}', 250.0)
            mu2 = data.get(f'p2_mu_{comp}', 250.0)
            fill1 = max(6, int(bar_area_w * min(mu1 / bar_max, 1.0)))
            fill2 = max(6, int(bar_area_w * min(mu2 / bar_max, 1.0)))

            # P1 bar — grows LEFT from center
            bar1_right = half - 50
            bar1_left = bar1_right - bar_area_w
            draw.rounded_rectangle(
                (bar1_left, by, bar1_right, by + bar_h_px),
                radius=6, fill=(40, 30, 30),
            )
            draw.rounded_rectangle(
                (bar1_right - fill1, by, bar1_right, by + bar_h_px),
                radius=6, fill=color,
            )
            # P1 value
            val1_str = f'{mu1:.0f}'
            draw.text((PADDING_X, by), val1_str, font=self.font_stat_label, fill=TEXT_SECONDARY)

            # P2 bar — grows RIGHT from center
            bar2_left = half + 50
            bar2_right = bar2_left + bar_area_w
            draw.rounded_rectangle(
                (bar2_left, by, bar2_right, by + bar_h_px),
                radius=6, fill=(22, 30, 48),
            )
            draw.rounded_rectangle(
                (bar2_left, by, bar2_left + fill2, by + bar_h_px),
                radius=6, fill=color,
            )
            # P2 value
            val2_str = f'{mu2:.0f}'
            self._text_right(draw, W - PADDING_X, by, val2_str, self.font_stat_label, TEXT_SECONDARY)

            # Skill label in center
            lbl = SKILL_LABELS[comp]
            self._text_center(draw, half, by, lbl, self.font_stat_label, TEXT_SECONDARY)

        # VS diamond
        vs_cy = y_vs + vs_h // 2 - 4
        d_r = 26
        diamond = [
            (half, vs_cy - d_r),
            (half + d_r, vs_cy),
            (half, vs_cy + d_r),
            (half - d_r, vs_cy),
        ]
        draw.polygon(diamond, fill=(20, 20, 30))
        draw.polygon(diamond, outline=ACCENT_RED, width=2)
        self._text_center(draw, half, vs_cy - 12, 'VS', self.font_label, TEXT_PRIMARY)

        # ── Score progress bar ────────────────────────────────────────────────
        y_bar = y_vs + vs_h
        draw.rectangle([(0, y_bar), (W, y_bar + score_bar_h)], fill=HEADER_BG)

        score_p1 = data.get('score_p1', 0)
        score_p2 = data.get('score_p2', 0)
        target = data.get('target_score', 1_000_000)

        bar_x = PADDING_X
        bar_w = W - 2 * PADDING_X
        bar_th = 6
        bar_ty = y_bar + (score_bar_h - bar_th) // 2

        # Draw background
        draw.rounded_rectangle((bar_x, bar_ty, bar_x + bar_w, bar_ty + bar_th), radius=3, fill=(40, 40, 60))

        # P1 fill (left)
        if target > 0:
            p1_fill = int(bar_w * min(score_p1 / target, 1.0))
            p2_fill = int(bar_w * min(score_p2 / target, 1.0))
            if p1_fill > 0:
                draw.rounded_rectangle((bar_x, bar_ty, bar_x + p1_fill, bar_ty + bar_th), radius=3, fill=P1_COLOR)
            if p2_fill > 0:
                # Draw from right
                draw.rounded_rectangle((bar_x + bar_w - p2_fill, bar_ty, bar_x + bar_w, bar_ty + bar_th), radius=3, fill=P2_COLOR)

        score_text = f'{int(score_p1):,}  vs  {int(score_p2):,}  /  {target:,}'
        self._text_center(draw, W // 2, y_bar + 6, score_text, self.font_stat_label, TEXT_SECONDARY)

        # Footer
        footer_y = H - footer_h
        self._draw_footer(draw, img, 'BIG BROTHER IS WATCHING YOUR RANK', footer_y, W)
        return self._save(img)

    # ─────────────────────────────────────────────────────────────────────────
    # ROUND RESULT CARD
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_round_result_card(self, data: Dict) -> BytesIO:
        """
        data keys:
          round_number, p1_name, p2_name
          winner  int (1 or 2)
          p1_points, p2_points  int
          p1_acc, p2_acc  float
          p1_combo, p2_combo  int
          p1_misses, p2_misses  int
          score_p1, score_p2  int  (running total)
          target_score  int
          beatmap_title str
          star_rating  float
          ml_winner  int | None
          ml_conf    float | None
        """
        W = CARD_WIDTH
        header_h = 36
        map_bar_h = 40
        result_h = 48     # winner banner
        rows_h = 4 * 34   # 4 stat rows
        score_bar_h = 44
        footer_h = 34
        H = header_h + map_bar_h + result_h + rows_h + score_bar_h + footer_h

        img, draw = self._create_canvas(W, H)

        round_num = data.get('round_number', 1)
        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL', f'Round {round_num} Result', W)

        p1_name = data.get('p1_name', 'P1')
        p2_name = data.get('p2_name', 'P2')
        winner = data.get('winner', 0)

        # ── Map info ─────────────────────────────────────────────────────────
        y = header_h
        draw.rectangle([(0, y), (W, y + map_bar_h)], fill=HEADER_BG)
        title = data.get('beatmap_title', 'Unknown')
        if len(title) > 55:
            title = title[:52] + '…'
        self._text_center(draw, W // 2, y + 4, title, self.font_small, TEXT_PRIMARY)
        stars = data.get('star_rating', 0.0)
        star_col = self._sr_color(stars)
        self._text_center(draw, W // 2, y + 22, f'{stars:.2f}★', self.font_stat_label, star_col)
        y += map_bar_h

        # ── Winner banner ─────────────────────────────────────────────────────
        winner_name = p1_name if winner == 1 else p2_name if winner == 2 else None
        winner_col = P1_COLOR if winner == 1 else P2_COLOR
        banner_bg = (38, 22, 22) if winner == 1 else (22, 30, 50) if winner == 2 else HEADER_BG
        draw.rectangle([(0, y), (W, y + result_h)], fill=banner_bg)
        draw.rectangle([(0, y), (W, y + 3)], fill=winner_col if winner else TEXT_SECONDARY)

        if winner_name:
            self._text_center(draw, W // 2, y + 6, '🏆  ROUND WINNER', self.font_stat_label, TEXT_SECONDARY)
            self._text_center(draw, W // 2, y + 22, winner_name, self.font_row, winner_col)
        else:
            self._text_center(draw, W // 2, y + 14, 'DRAW', self.font_row, TEXT_SECONDARY)
        y += result_h

        # ── Stat rows ─────────────────────────────────────────────────────────
        stat_rows = [
            ('POINTS',  f"{data.get('p1_points', 0):,}",  f"{data.get('p2_points', 0):,}"),
            ('ACC',     f"{data.get('p1_acc', 0.0):.2f}%", f"{data.get('p2_acc', 0.0):.2f}%"),
            ('COMBO',   f"{data.get('p1_combo', 0)}x",    f"{data.get('p2_combo', 0)}x"),
            ('MISSES',  str(data.get('p1_misses', 0)),    str(data.get('p2_misses', 0))),
        ]
        row_h = 34
        half = W // 2
        for i, (label, v1, v2) in enumerate(stat_rows):
            ry = y + i * row_h
            row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
            draw.rectangle([(0, ry), (W, ry + row_h)], fill=row_bg)

            # Winner col highlight
            p1_col = (ACCENT_GREEN if winner == 1 else TEXT_SECONDARY) if label not in ('MISSES',) else \
                     (ACCENT_GREEN if data.get('p1_misses', 0) <= data.get('p2_misses', 0) else TEXT_SECONDARY)
            p2_col = (ACCENT_GREEN if winner == 2 else TEXT_SECONDARY) if label not in ('MISSES',) else \
                     (ACCENT_GREEN if data.get('p2_misses', 0) <= data.get('p1_misses', 0) else TEXT_SECONDARY)
            if label == 'MISSES':
                p1_col = ACCENT_GREEN if data.get('p1_misses', 0) <= data.get('p2_misses', 0) else (180, 80, 80)
                p2_col = ACCENT_GREEN if data.get('p2_misses', 0) <= data.get('p1_misses', 0) else (180, 80, 80)

            draw.text((PADDING_X, ry + 9), v1, font=self.font_label, fill=p1_col)
            self._text_center(draw, half, ry + 9, label, self.font_stat_label, TEXT_SECONDARY)
            self._text_right(draw, W - PADDING_X, ry + 9, v2, self.font_label, p2_col)

        y += len(stat_rows) * row_h

        # ── Score progress ────────────────────────────────────────────────────
        draw.rectangle([(0, y), (W, y + score_bar_h)], fill=HEADER_BG)

        score_p1 = data.get('score_p1', 0)
        score_p2 = data.get('score_p2', 0)
        target = data.get('target_score', 1_000_000)

        # Score text
        self._text_center(draw, W // 2, y + 4, f'{int(score_p1):,}  :  {int(score_p2):,}', self.font_row, TEXT_PRIMARY)

        # Progress bar
        bar_x = PADDING_X
        bar_w = W - 2 * PADDING_X
        bar_th = 6
        bar_ty = y + 28
        draw.rounded_rectangle((bar_x, bar_ty, bar_x + bar_w, bar_ty + bar_th), radius=3, fill=(40, 40, 60))
        if target > 0:
            p1_fill = int(bar_w * min(score_p1 / target, 1.0))
            p2_fill = int(bar_w * min(score_p2 / target, 1.0))
            if p1_fill > 0:
                draw.rounded_rectangle((bar_x, bar_ty, bar_x + p1_fill, bar_ty + bar_th), radius=3, fill=P1_COLOR)
            if p2_fill > 0:
                draw.rounded_rectangle((bar_x + bar_w - p2_fill, bar_ty, bar_x + bar_w, bar_ty + bar_th), radius=3, fill=P2_COLOR)

        # Target label
        target_str = f'target: {target:,}'
        self._text_center(draw, W // 2, bar_ty + 10, target_str, self.font_stat_label, (80, 80, 100))

        # P1/P2 name labels under bar
        draw.text((PADDING_X, bar_ty + 10), p1_name, font=self.font_stat_label, fill=P1_COLOR)
        self._text_right(draw, W - PADDING_X, bar_ty + 10, p2_name, self.font_stat_label, P2_COLOR)

        # Footer
        footer_y = H - footer_h
        self._draw_footer(draw, img, 'BIG BROTHER IS WATCHING YOUR RANK', footer_y, W)
        return self._save(img)

    # ─────────────────────────────────────────────────────────────────────────
    # DUEL END CARD
    # ─────────────────────────────────────────────────────────────────────────

    def generate_bsk_duel_end_card(self, data: Dict) -> BytesIO:
        """
        data keys:
          p1_name, p2_name  str
          winner  int (1 or 2 or 0 for draw)
          score_p1, score_p2  int
          mode  str
          total_rounds  int
          is_test  bool
          # Rating deltas (optional, only for ranked)
          p1_delta_{aim,speed,acc,cons}  float
          p2_delta_{aim,speed,acc,cons}  float
          # Round history
          rounds  list[dict]: round_number, beatmap_title, star_rating, winner, p1_points, p2_points
        """
        W = CARD_WIDTH
        header_h = 36
        winner_h = 90
        score_h = 40
        ratings_h = 80       # 4 skill delta panels
        rounds = data.get('rounds', [])
        round_row_h = 40
        rounds_h = len(rounds) * round_row_h + 16 if rounds else 0
        footer_h = 34
        H = header_h + winner_h + score_h + ratings_h + rounds_h + footer_h

        img, draw = self._create_canvas(W, H)
        self._draw_header(draw, 'PROJECT 1984 — BEATSKILL DUEL', 'Final Result', W)

        p1_name = data.get('p1_name', 'P1')
        p2_name = data.get('p2_name', 'P2')
        winner = data.get('winner', 0)
        score_p1 = int(data.get('score_p1', 0))
        score_p2 = int(data.get('score_p2', 0))
        mode = data.get('mode', 'casual').upper()
        total_rounds = data.get('total_rounds', 0)
        is_test = data.get('is_test', False)

        # ── Winner banner ─────────────────────────────────────────────────────
        y = header_h
        winner_name = (p1_name if winner == 1 else p2_name) if winner else None
        winner_col = P1_COLOR if winner == 1 else P2_COLOR if winner == 2 else TEXT_SECONDARY
        banner_bg = (38, 18, 18) if winner == 1 else (18, 28, 52) if winner == 2 else HEADER_BG
        draw.rectangle([(0, y), (W, y + winner_h)], fill=banner_bg)
        draw.rectangle([(0, y), (W, y + 4)], fill=winner_col)

        if winner_name:
            self._text_center(draw, W // 2, y + 10, '🏆  ПОБЕДИТЕЛЬ' + (' [ТЕСТ]' if is_test else ''), self.font_stat_label, TEXT_SECONDARY)
            self._text_center(draw, W // 2, y + 30, winner_name, self.font_big, winner_col)
            loser = p2_name if winner == 1 else p1_name
            self._text_center(draw, W // 2, y + 66, f'defeated {loser}', self.font_small, TEXT_SECONDARY)
        else:
            self._text_center(draw, W // 2, y + 28, 'НИЧЬЯ' + (' [ТЕСТ]' if is_test else ''), self.font_big, TEXT_SECONDARY)

        # Mode / rounds info right-aligned
        info_str = f'{mode} · {total_rounds} rounds'
        self._text_right(draw, W - PADDING_X, y + 12, info_str, self.font_stat_label, TEXT_SECONDARY)
        y += winner_h

        # ── Score bar ─────────────────────────────────────────────────────────
        draw.rectangle([(0, y), (W, y + score_h)], fill=HEADER_BG)
        score_str = f'{score_p1:,}  :  {score_p2:,}'
        self._text_center(draw, W // 2, y + 4, score_str, self.font_row, TEXT_PRIMARY)

        # Comparison bar
        bar_x = PADDING_X
        bar_w = W - 2 * PADDING_X
        bar_th = 6
        bar_ty = y + 28
        total_sc = score_p1 + score_p2
        draw.rounded_rectangle((bar_x, bar_ty, bar_x + bar_w, bar_ty + bar_th), radius=3, fill=(40, 40, 60))
        if total_sc > 0:
            ratio = score_p1 / total_sc
            split = int(bar_w * ratio)
            if split > 0:
                draw.rounded_rectangle((bar_x, bar_ty, bar_x + split - 1, bar_ty + bar_th), radius=3, fill=P1_COLOR)
            if split < bar_w:
                draw.rounded_rectangle((bar_x + split + 1, bar_ty, bar_x + bar_w, bar_ty + bar_th), radius=3, fill=P2_COLOR)
        draw.text((PADDING_X, bar_ty + 10), p1_name, font=self.font_stat_label, fill=P1_COLOR)
        self._text_right(draw, W - PADDING_X, bar_ty + 10, p2_name, self.font_stat_label, P2_COLOR)
        y += score_h

        # ── Rating deltas ─────────────────────────────────────────────────────
        draw.rectangle([(0, y), (W, y + ratings_h)], fill=(22, 22, 36))
        has_deltas = any(data.get(f'p1_delta_{c}') is not None for c in SKILL_KEYS)

        if has_deltas and not is_test:
            panel_count = 4
            panel_gap = 8
            panel_w = (W - 2 * PADDING_X - (panel_count - 1) * panel_gap) // panel_count

            for i, comp in enumerate(SKILL_KEYS):
                px = PADDING_X + i * (panel_w + panel_gap)
                py = y + 8
                ph = ratings_h - 16
                color = SKILL_COLORS[comp]

                draw.rounded_rectangle((px, py, px + panel_w, py + ph), radius=6, fill=(28, 28, 46))
                draw.rounded_rectangle((px, py, px + panel_w, py + 3), radius=2, fill=color)

                lbl = SKILL_LABELS[comp]
                self._text_center(draw, px + panel_w // 2, py + 6, lbl, self.font_stat_label, TEXT_SECONDARY)

                d1 = data.get(f'p1_delta_{comp}', 0) or 0
                d2 = data.get(f'p2_delta_{comp}', 0) or 0

                def fmt_delta(d):
                    return f'+{d:.1f}' if d >= 0 else f'{d:.1f}'

                d1_str = fmt_delta(d1)
                d2_str = fmt_delta(d2)
                d1_col = ACCENT_GREEN if d1 >= 0 else (180, 60, 60)
                d2_col = ACCENT_GREEN if d2 >= 0 else (180, 60, 60)

                # P1 delta (left-aligned inside panel)
                draw.text((px + 6, py + 28), d1_str, font=self.font_small, fill=d1_col)
                draw.text((px + 6, py + 46), p1_name[:7], font=self.font_stat_label, fill=P1_COLOR)
                # P2 delta (right-aligned inside panel)
                self._text_right(draw, px + panel_w - 6, py + 28, d2_str, self.font_small, d2_col)
                self._text_right(draw, px + panel_w - 6, py + 46, p2_name[:7], self.font_stat_label, P2_COLOR)
        else:
            msg = 'Рейтинг не изменён (тестовая дуэль)' if is_test else 'Изменения рейтинга недоступны'
            self._text_center(draw, W // 2, y + ratings_h // 2 - 8, msg, self.font_label, TEXT_SECONDARY)

        y += ratings_h

        # ── Round history ─────────────────────────────────────────────────────
        if rounds:
            draw.line([(PADDING_X, y + 6), (W - PADDING_X, y + 6)], fill=(50, 50, 70), width=1)
            y += 14
            for i, rnd in enumerate(rounds):
                ry = y + i * round_row_h
                row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
                draw.rectangle([(0, ry), (W, ry + round_row_h)], fill=row_bg)

                rnum = rnd.get('round_number', i + 1)
                rtitle = rnd.get('beatmap_title', 'Unknown')
                if len(rtitle) > 40:
                    rtitle = rtitle[:37] + '…'
                rsr = rnd.get('star_rating', 0.0)
                rwinner = rnd.get('winner', 0)
                rp1 = rnd.get('p1_points', 0)
                rp2 = rnd.get('p2_points', 0)

                # Round number badge
                badge_w = 26
                draw.rounded_rectangle(
                    (PADDING_X, ry + 7, PADDING_X + badge_w, ry + round_row_h - 7),
                    radius=4, fill=ACCENT_RED,
                )
                self._text_center(draw, PADDING_X + badge_w // 2, ry + 9,
                                   str(rnum), self.font_stat_label, TEXT_PRIMARY)

                # Map title & SR
                info_x = PADDING_X + badge_w + 8
                sr_col = self._sr_color(rsr)
                draw.text((info_x, ry + 6), rtitle, font=self.font_stat_label, fill=TEXT_PRIMARY)
                draw.text((info_x, ry + 22), f'{rsr:.1f}★', font=self.font_stat_label, fill=sr_col)

                # Points on right
                pts_col1 = ACCENT_GREEN if rwinner == 1 else TEXT_SECONDARY
                pts_col2 = ACCENT_GREEN if rwinner == 2 else TEXT_SECONDARY
                pts_str = f'{rp1:,}  :  {rp2:,}'
                self._text_right(draw, W - PADDING_X, ry + 12, pts_str, self.font_small, TEXT_PRIMARY)

        # Footer
        footer_y = H - footer_h
        self._draw_footer(draw, img, 'BIG BROTHER IS WATCHING YOUR RANK', footer_y, W)
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
