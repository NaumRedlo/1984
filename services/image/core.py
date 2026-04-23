"""
Pillow-based card generators (1984 dystopia theme).

BaseCardRenderer — shared primitives (fonts, header, footer, separators).
+ 5-page profile cards, compare card with avatars, recent/hps/bounty cards.
"""

import asyncio
import os
from io import BytesIO
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple

import aiohttp
from PIL import Image, ImageDraw, ImageFont

from utils.logger import get_logger

# Re-export from extracted modules for backward compatibility
from services.image.constants import (  # noqa: F401
    BG_COLOR, HEADER_BG, ROW_EVEN, ROW_ODD, TEXT_PRIMARY, TEXT_SECONDARY,
    ACCENT_RED, ACCENT_GREEN, SECTION_BG, PANEL_BG,
    TOP_COLORS, GRADE_COLORS, MOD_COLORS, MONTH_NAMES,
    CARD_WIDTH, HEADER_HEIGHT, ROW_HEIGHT, FOOTER_HEIGHT, PADDING_X, VALUE_RIGHT_X,
    ASSETS_DIR, FONT_DIR, TORUS_BOLD, TORUS_SEMI, TORUS_REG, HUNINN,
    FLAGS_DIR, ICONS_DIR, FALLBACK_CANDIDATES,
)
from services.image.utils import (  # noqa: F401
    load_icon, load_flag, _find_font, _none_coro,
    _get_shared_session, close_shared_session, download_image,
    rounded_rect_crop, cover_center_crop, draw_cover_background, draw_line_graph,
    MAX_IMAGE_BYTES,
)
from services.image.base import BaseCardRenderer as _BaseCardRenderer  # noqa: F401
from services.image.render.profile import ProfileCardMixin
from services.image.render.recent import RecentCardMixin
from services.image.render.hps import HpsCardMixin
from services.image.render.bounty import BountyCardMixin
from services.image.render.compare import CompareCardMixin
from services.image.render.help import HelpCardMixin
from services.image.render.bsk import BskCardMixin

logger = get_logger("services.image_gen")

# Re-export BaseCardRenderer for backward compatibility
BaseCardRenderer = _BaseCardRenderer


class _CardRendererMixin(ProfileCardMixin, RecentCardMixin, HpsCardMixin, BountyCardMixin, CompareCardMixin, HelpCardMixin, BskCardMixin, _BaseCardRenderer):
    """All domain-specific card methods. Will be split further in later steps."""

    # Duel Cards

    def _draw_score_comparison_bar(
        self, draw: ImageDraw.Draw, y: int, w: int,
        p1_val: float, p2_val: float,
        bar_h: int = 6, color1=(200, 80, 80), color2=(80, 120, 200),
    ):
        """Draw a horizontal score comparison bar — wider side = higher value."""
        total = p1_val + p2_val
        if total <= 0:
            ratio = 0.5
        else:
            ratio = p1_val / total
        bar_x = PADDING_X
        bar_w = w - 2 * PADDING_X
        split = int(bar_w * ratio)

        # Left side (player 1)
        if split > 0:
            draw.rounded_rectangle(
                (bar_x, y, bar_x + split - 1, y + bar_h),
                radius=3, fill=color1,
            )
        # Right side (player 2)
        if split < bar_w:
            draw.rounded_rectangle(
                (bar_x + split + 1, y, bar_x + bar_w, y + bar_h),
                radius=3, fill=color2,
            )

    def _draw_win_dots(self, draw: ImageDraw.Draw, cx: int, y: int, wins: int, needed: int, color):
        """Draw filled/empty circles representing round wins (like tennis sets)."""
        dot_r = 6
        gap = 20
        total_w = (needed - 1) * gap
        start_x = cx - total_w // 2
        for i in range(needed):
            dx = start_x + i * gap
            if i < wins:
                draw.ellipse((dx - dot_r, y - dot_r, dx + dot_r, y + dot_r), fill=color)
            else:
                draw.ellipse((dx - dot_r, y - dot_r, dx + dot_r, y + dot_r), outline=color, width=2)

    def generate_duel_round_card(self, data: Dict) -> BytesIO:
        """PNG card for a single duel round result — polished layout."""
        W = CARD_WIDTH
        header_h = 36
        map_section_h = 54
        player_section_h = 120
        bar_section_h = 20
        score_section_h = 70
        footer_h = 34
        H = header_h + map_section_h + player_section_h + bar_section_h + score_section_h + footer_h

        img, draw = self._create_canvas(W, H)

        round_num = data.get("round_number", 1)
        best_of = data.get("best_of", 5)
        self._draw_header(draw, "PROJECT 1984 — DUEL", f"Round {round_num} / Bo{best_of}", W)

        # Map info bar
        y = header_h
        draw.rectangle([(0, y), (W, y + map_section_h)], fill=HEADER_BG)
        beatmap_title = data.get("beatmap_title", "Unknown Map")
        if len(beatmap_title) > 55:
            beatmap_title = beatmap_title[:52] + "..."
        self._text_center(draw, W // 2, y + 8, beatmap_title, self.font_label, TEXT_PRIMARY)
        star_rating = data.get("star_rating", 0.0)
        star_icon = load_icon("star", size=14)
        star_text = f"{star_rating:.2f}"
        if star_icon:
            star_bbox = draw.textbbox((0, 0), star_text, font=self.font_small)
            total_w = star_icon.width + 4 + (star_bbox[2] - star_bbox[0])
            sx = W // 2 - total_w // 2
            img.paste(star_icon, (sx, y + 31), star_icon)
            draw = ImageDraw.Draw(img)
            draw.text((sx + star_icon.width + 4, y + 30), star_text, font=self.font_small, fill=(255, 204, 50))
        else:
            self._text_center(draw, W // 2, y + 30, f"★ {star_text}", self.font_small, (255, 204, 50))

        # Player blocks (side by side)
        y += map_section_h
        half_w = W // 2
        round_winner = data.get("round_winner", 0)

        p1_name = data.get("player1_name", "Player 1")
        p2_name = data.get("player2_name", "Player 2")
        p1_score_val = data.get("player1_score", 0)
        p2_score_val = data.get("player2_score", 0)
        p1_acc = data.get("player1_accuracy", 0.0)
        p2_acc = data.get("player2_accuracy", 0.0)
        p1_combo = data.get("player1_combo", 0)
        p2_combo = data.get("player2_combo", 0)

        # Winner highlight colors
        p1_accent = ACCENT_GREEN if round_winner == 1 else ACCENT_RED if round_winner == 2 else TEXT_SECONDARY
        p2_accent = ACCENT_GREEN if round_winner == 2 else ACCENT_RED if round_winner == 1 else TEXT_SECONDARY

        # Panel backgrounds — winner gets a subtle green tint
        p1_bg = (28, 42, 28) if round_winner == 1 else PANEL_BG
        p2_bg = (28, 42, 28) if round_winner == 2 else PANEL_BG

        # Left panel (P1)
        self._draw_panel(draw, 8, y + 6, half_w - 16, player_section_h - 12, p1_bg)
        # Winner/loser indicator stripe at top of panel
        draw.rectangle([(8, y + 6), (half_w - 8, y + 9)], fill=p1_accent)

        draw.text((24, y + 18), p1_name, font=self.font_subtitle, fill=TEXT_PRIMARY)
        draw.text((24, y + 44), f"{p1_score_val:,}", font=self.font_big, fill=p1_accent)
        draw.text((24, y + 82), f"{p1_acc:.2f}%", font=self.font_label, fill=TEXT_SECONDARY)
        p1_acc_bbox = draw.textbbox((0, 0), f"{p1_acc:.2f}%", font=self.font_label)
        p1_acc_w = p1_acc_bbox[2] - p1_acc_bbox[0]
        draw.text((24 + p1_acc_w + 16, y + 82), f"{p1_combo:,}x", font=self.font_label, fill=TEXT_SECONDARY)

        # Right panel (P2) — mirrored
        self._draw_panel(draw, half_w + 8, y + 6, half_w - 16, player_section_h - 12, p2_bg)
        draw.rectangle([(half_w + 8, y + 6), (W - 8, y + 9)], fill=p2_accent)

        self._text_right(draw, W - 24, y + 18, p2_name, self.font_subtitle, TEXT_PRIMARY)
        self._text_right(draw, W - 24, y + 44, f"{p2_score_val:,}", self.font_big, p2_accent)
        combo_str = f"{p2_combo:,}x"
        combo_bbox = draw.textbbox((0, 0), combo_str, font=self.font_label)
        combo_w = combo_bbox[2] - combo_bbox[0]
        p2_acc_str = f"{p2_acc:.2f}%"
        p2_acc_bbox = draw.textbbox((0, 0), p2_acc_str, font=self.font_label)
        p2_acc_w = p2_acc_bbox[2] - p2_acc_bbox[0]
        self._text_right(draw, W - 24, y + 82, p2_acc_str, self.font_label, TEXT_SECONDARY)
        self._text_right(draw, W - 24 - combo_w - 16 - p2_acc_w, y + 82, combo_str, self.font_label, TEXT_SECONDARY)

        # "VS" diamond in center
        vs_cy = y + player_section_h // 2
        diamond_r = 22
        diamond = [
            (half_w, vs_cy - diamond_r),
            (half_w + diamond_r, vs_cy),
            (half_w, vs_cy + diamond_r),
            (half_w - diamond_r, vs_cy),
        ]
        draw.polygon(diamond, fill=ACCENT_RED)
        self._text_center(draw, half_w, vs_cy - 10, "VS", self.font_label, TEXT_PRIMARY)

        # Score comparison bar
        y += player_section_h
        self._draw_score_comparison_bar(
            draw, y + 7, W,
            float(p1_score_val), float(p2_score_val),
            bar_h=6,
            color1=p1_accent, color2=p2_accent,
        )

        # Duel series score
        y += bar_section_h
        draw.rectangle([(0, y), (W, y + score_section_h)], fill=HEADER_BG)

        p1_wins = data.get("player1_wins", 0)
        p2_wins = data.get("player2_wins", 0)
        wins_needed = best_of // 2 + 1

        # Win dots for P1 (left of center)
        self._draw_win_dots(draw, half_w // 2, y + 20, p1_wins, wins_needed, p1_accent)
        # Win dots for P2 (right of center)
        self._draw_win_dots(draw, half_w + half_w // 2, y + 20, p2_wins, wins_needed, p2_accent)

        # Score text
        score_text = f"{p1_wins}  :  {p2_wins}"
        self._text_center(draw, half_w, y + 14, score_text, self.font_big, TEXT_PRIMARY)
        self._text_center(draw, half_w, y + 48, f"Best of {best_of}", self.font_small, TEXT_SECONDARY)

        # Names under dots
        draw.text((24, y + 44), p1_name, font=self.font_small, fill=TEXT_SECONDARY)
        self._text_right(draw, W - 24, y + 44, p2_name, self.font_small, TEXT_SECONDARY)

        return self._save(img)

    def generate_duel_result_card(self, data: Dict) -> BytesIO:
        """PNG card for final duel result — polished layout."""
        W = CARD_WIDTH
        header_h = 36
        winner_section_h = 100
        score_section_h = 50
        rounds_row_h = 52
        rounds = data.get("rounds", [])
        rounds_section_h = len(rounds) * rounds_row_h + 16 if rounds else 0
        footer_h = 34
        H = header_h + winner_section_h + score_section_h + rounds_section_h + footer_h

        img, draw = self._create_canvas(W, H)
        self._draw_header(draw, "PROJECT 1984 — DUEL RESULT", "", W)

        p1_name = data.get("player1_name", "Player 1")
        p2_name = data.get("player2_name", "Player 2")
        p1_wins = data.get("player1_wins", 0)
        p2_wins = data.get("player2_wins", 0)
        winner_name = data.get("winner_name", "DRAW")
        best_of = data.get("best_of", 5)

        # Winner banner
        y = header_h
        if winner_name == "DRAW":
            draw.rectangle([(0, y), (W, y + winner_section_h)], fill=HEADER_BG)
            self._text_center(draw, W // 2, y + 20, "DRAW", self.font_big, TEXT_SECONDARY)
            self._text_center(draw, W // 2, y + 60, f"{p1_name}  vs  {p2_name}", self.font_label, TEXT_SECONDARY)
        else:
            # Gradient-ish winner bg
            winner_bg = (25, 45, 25)
            draw.rectangle([(0, y), (W, y + winner_section_h)], fill=winner_bg)
            # Green accent line at top
            draw.rectangle([(0, y), (W, y + 3)], fill=ACCENT_GREEN)

            self._text_center(draw, W // 2, y + 10, "WINNER", self.font_small, ACCENT_GREEN)
            self._text_center(draw, W // 2, y + 30, winner_name, self.font_big, TEXT_PRIMARY)

            # Loser name smaller below
            loser_name = p2_name if winner_name == p1_name else p1_name
            self._text_center(draw, W // 2, y + 68, f"defeated {loser_name}", self.font_label, TEXT_SECONDARY)

        # Series score with dots
        y += winner_section_h
        draw.rectangle([(0, y), (W, y + score_section_h)], fill=HEADER_BG)

        half_w = W // 2
        wins_needed = best_of // 2 + 1

        p1_color = ACCENT_GREEN if p1_wins > p2_wins else ACCENT_RED if p2_wins > p1_wins else TEXT_SECONDARY
        p2_color = ACCENT_GREEN if p2_wins > p1_wins else ACCENT_RED if p1_wins > p2_wins else TEXT_SECONDARY

        score_text = f"{p1_wins}  :  {p2_wins}"
        self._text_center(draw, half_w, y + 6, score_text, self.font_big, TEXT_PRIMARY)

        self._draw_win_dots(draw, half_w // 2, y + 38, p1_wins, wins_needed, p1_color)
        self._draw_win_dots(draw, half_w + half_w // 2, y + 38, p2_wins, wins_needed, p2_color)

        draw.text((24, y + 30), p1_name, font=self.font_small, fill=TEXT_SECONDARY)
        self._text_right(draw, W - 24, y + 30, p2_name, self.font_small, TEXT_SECONDARY)

        # Round list
        y += score_section_h
        if rounds:
            draw.line([(PADDING_X, y), (W - PADDING_X, y)], fill=ACCENT_RED, width=1)
            y += 8
            for i, rnd in enumerate(rounds):
                row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
                draw.rectangle([(0, y), (W, y + rounds_row_h)], fill=row_bg)

                r_num = rnd.get("round_number", i + 1)
                r_map = rnd.get("beatmap_title", "Unknown")
                if len(r_map) > 35:
                    r_map = r_map[:32] + "..."
                r_stars = rnd.get("star_rating", 0.0)
                r_winner = rnd.get("winner_name", "—")
                winner_player = rnd.get("winner_player", 0)
                p1_sc = rnd.get("player1_score", 0)
                p2_sc = rnd.get("player2_score", 0)

                # Round number badge
                badge_x = PADDING_X
                badge_w = 30
                draw.rounded_rectangle(
                    (badge_x, y + 4, badge_x + badge_w, y + badge_w + 4),
                    radius=4, fill=ACCENT_RED,
                )
                self._text_center(draw, badge_x + badge_w // 2, y + 7, str(r_num), self.font_small, TEXT_PRIMARY)

                # Map name + star rating (top line)
                info_x = badge_x + badge_w + 10
                star_icon = load_icon("star", size=16)
                if r_stars > 0 and star_icon:
                    draw.text((info_x, y + 4), f"{r_stars:.1f}", font=self.font_label, fill=TEXT_PRIMARY)
                    val_bbox = draw.textbbox((0, 0), f"{r_stars:.1f}", font=self.font_label)
                    val_w = val_bbox[2] - val_bbox[0]
                    img.paste(star_icon, (info_x + val_w + 4, y + 7), star_icon)
                    draw = ImageDraw.Draw(img)
                    map_x = info_x + val_w + 4 + star_icon.width + 6
                else:
                    star_prefix = f"{r_stars:.1f}★ " if r_stars > 0 else ""
                    draw.text((info_x, y + 4), f"{star_prefix}{r_map}", font=self.font_label, fill=TEXT_PRIMARY)
                    map_x = None
                if map_x is not None:
                    draw.text((map_x, y + 4), r_map, font=self.font_label, fill=TEXT_PRIMARY)

                # Scores (bottom line): "1,234,567 vs 987,654"
                p1_sc_str = f"{p1_sc:,}" if p1_sc > 0 else "—"
                p2_sc_str = f"{p2_sc:,}" if p2_sc > 0 else "—"
                p1_sc_color = ACCENT_GREEN if winner_player == 1 else TEXT_SECONDARY
                p2_sc_color = ACCENT_GREEN if winner_player == 2 else TEXT_SECONDARY
                draw.text((info_x, y + 26), p1_sc_str, font=self.font_small, fill=p1_sc_color)
                vs_bbox = draw.textbbox((0, 0), p1_sc_str, font=self.font_small)
                vs_x = info_x + vs_bbox[2] - vs_bbox[0] + 4
                draw.text((vs_x, y + 26), "vs", font=self.font_stat_label, fill=TEXT_SECONDARY)
                vs2_bbox = draw.textbbox((0, 0), "vs", font=self.font_stat_label)
                p2_x = vs_x + vs2_bbox[2] - vs2_bbox[0] + 4
                draw.text((p2_x, y + 26), p2_sc_str, font=self.font_small, fill=p2_sc_color)

                # Winner indicator on right
                r_color = ACCENT_GREEN if winner_player == 1 else ACCENT_RED if winner_player == 2 else TEXT_SECONDARY
                self._text_right(draw, W - PADDING_X, y + 12, r_winner, self.font_label, r_color)

                y += rounds_row_h
            y += 8

        return self._save(img)

    def generate_duel_history_card(self, data: Dict) -> BytesIO:
        """PNG card for recent completed duel history."""
        entries = data.get("duels", [])
        header_h = 36
        row_h = 58
        H = header_h + max(len(entries), 1) * row_h + 12
        W = CARD_WIDTH

        img, draw = self._create_canvas(W, H)
        self._draw_header(draw, "PROJECT 1984 — DUEL HISTORY", "Recent completed duels", W)

        if not entries:
            draw.text((PADDING_X, header_h + 24), "No completed duels yet.", font=self.font_row, fill=TEXT_SECONDARY)
        else:
            y = header_h + 8
            for i, duel in enumerate(entries):
                row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
                draw.rectangle([(0, y), (W, y + row_h)], fill=row_bg)

                opponent = duel.get("opponent_name", "—")
                result = duel.get("result", "—")
                best_of = duel.get("best_of", 0)
                completed_at = duel.get("completed_at")
                score_line = duel.get("score_line", "")

                if isinstance(completed_at, datetime):
                    if completed_at.tzinfo is None:
                        completed_at = completed_at.replace(tzinfo=timezone.utc)
                    when = completed_at.astimezone(timezone.utc).strftime("%d.%m %H:%M UTC")
                elif completed_at:
                    when = str(completed_at)
                else:
                    when = "—"

                result_color = ACCENT_GREEN if result == "Win" else ACCENT_RED if result == "Loss" else TEXT_SECONDARY
                draw.text((PADDING_X, y + 10), opponent, font=self.font_row, fill=TEXT_PRIMARY)
                draw.text((PADDING_X, y + 32), f"{result} • BO{best_of} • {when}", font=self.font_small, fill=result_color)
                self._text_right(draw, W - PADDING_X, y + 12, score_line, self.font_row, TEXT_PRIMARY)
                y += row_h

        return self._save(img)

    def generate_duel_stats_card(self, data: Dict) -> BytesIO:
        """PNG card for duel summary stats and recent results."""
        summary = data.get("summary", {})
        entries = data.get("duels", [])
        header_h = 36
        panel_h = 64
        gap = 10
        summary_y = header_h + 10
        rows_y = summary_y + panel_h + 16
        row_h = 54
        H = rows_y + max(len(entries), 1) * row_h + 14
        W = CARD_WIDTH

        img, draw = self._create_canvas(W, H)
        self._draw_header(draw, "PROJECT 1984 — DUEL STATS", "Wins, formats, and recent duels", W)

        panel_w = (W - 2 * PADDING_X - 2 * gap) // 3
        panels = [
            (f"{summary.get('wins', 0):,}", "WINS"),
            (f"{summary.get('losses', 0):,}", "LOSSES"),
            (f"{summary.get('draws', 0):,}", "DRAWS"),
        ]
        for idx, (value, label) in enumerate(panels):
            px = PADDING_X + idx * (panel_w + gap)
            self._draw_panel(draw, px, summary_y, panel_w, panel_h)
            self._draw_stat_cell(draw, px + panel_w // 2, summary_y + 8, value, label)

        extra_y = summary_y + panel_h + 14
        win_rate = summary.get("win_rate")
        formats = ", ".join(summary.get("formats", [])) or "—"
        self._draw_panel(draw, PADDING_X, extra_y, W - 2 * PADDING_X, 52)
        win_rate_text = f"{win_rate:.1f}%" if win_rate is not None else "—"
        self._draw_kv_row(draw, extra_y + 8, "Win rate", win_rate_text, label_font=self.font_ru_label, value_font=self.font_row)
        self._text_right(draw, W - PADDING_X - 2, extra_y + 8, formats, self.font_small, TEXT_SECONDARY)

        y = rows_y
        if not entries:
            draw.text((PADDING_X, y + 12), "No completed duels yet.", font=self.font_row, fill=TEXT_SECONDARY)
        else:
            for i, duel in enumerate(entries):
                row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
                draw.rectangle([(0, y), (W, y + row_h)], fill=row_bg)

                opponent = duel.get("opponent_name", "—")
                result = duel.get("result", "—")
                best_of = duel.get("best_of", 0)
                completed_at = duel.get("completed_at")
                score_line = duel.get("score_line", "")

                if isinstance(completed_at, datetime):
                    if completed_at.tzinfo is None:
                        completed_at = completed_at.replace(tzinfo=timezone.utc)
                    when = completed_at.astimezone(timezone.utc).strftime("%d.%m %H:%M UTC")
                elif completed_at:
                    when = str(completed_at)
                else:
                    when = "—"

                result_color = ACCENT_GREEN if result == "Win" else ACCENT_RED if result == "Loss" else TEXT_SECONDARY
                draw.text((PADDING_X, y + 10), opponent, font=self.font_row, fill=TEXT_PRIMARY)
                draw.text((PADDING_X, y + 30), f"{result} • BO{best_of} • {when}", font=self.font_small, fill=result_color)
                self._text_right(draw, W - PADDING_X, y + 10, score_line, self.font_row, TEXT_PRIMARY)
                y += row_h

        return self._save(img)

    def generate_duel_pick_card(self, data: Dict) -> BytesIO:
        W, H = 800, 360
        img, draw = self._create_canvas(W, H)
        header_h = 36
        draw.rectangle([(0, 0), (W, header_h)], fill=HEADER_BG)
        self._text_center(draw, W // 2, 8, 'PROJECT 1984 — DUEL MAP PICK', self.font_subtitle, ACCENT_RED)
        draw.line([(0, header_h - 2), (W, header_h - 2)], fill=ACCENT_RED, width=2)

        pick_turn = data.get('pick_turn', '—')
        round_no = data.get('round_number', 1)
        self._text_center(draw, W // 2, 48, f'Раунд {round_no} — выбирает {pick_turn}', self.font_label, TEXT_PRIMARY)

        suggestions = data.get('suggestions', [])
        start_y = 82
        row_h = 44
        for idx, s in enumerate(suggestions[:5]):
            y = start_y + idx * (row_h + 6)
            draw.rounded_rectangle((PADDING_X, y, W - PADDING_X, y + row_h), radius=10, fill=ROW_EVEN if idx % 2 == 0 else ROW_ODD)
            self._text_center(draw, PADDING_X + 20, y + 11, str(idx + 1), self.font_label, ACCENT_RED)
            title = s.get('title', 'Unknown')
            stars = s.get('star_rating', 0.0)
            if len(title) > 38:
                title = title[:35] + '...'
            draw.text((PADDING_X + 44, y + 10), title, font=self.font_label, fill=TEXT_PRIMARY)
            self._text_right(draw, W - PADDING_X - 12, y + 10, f'{stars:.1f}★', self.font_label, TEXT_SECONDARY)
            self._text_center(draw, W - PADDING_X - 54, y + 12, 'PICK', self.font_stat_label, ACCENT_RED)

        return self._save(img)

    async def generate_duel_pick_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_duel_pick_card, data)

    async def generate_duel_round_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_duel_round_card, data)

    async def generate_duel_result_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_duel_result_card, data)

    async def generate_duel_history_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_duel_history_card, data)

    async def generate_duel_stats_card_async(self, data: Dict) -> BytesIO:
        return await asyncio.to_thread(self.generate_duel_stats_card, data)


from services.image.leaderboard import LeaderboardCardGenerator  # noqa: E402


class CardRenderer(_CardRendererMixin, LeaderboardCardGenerator):
    """Backward-compatible facade combining all card generators."""


card_renderer = CardRenderer()
