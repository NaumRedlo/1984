"""DUEL profile card renderer."""

import asyncio
from io import BytesIO
from typing import Dict, Optional

from PIL import Image, ImageDraw

from services.image.constants import (
    BG_COLOR, HEADER_BG, TEXT_PRIMARY, TEXT_SECONDARY,
    ACCENT_RED, ACCENT_GREEN, PADDING_X,
)
from services.image.utils import download_image, rounded_rect_crop, load_flag, cover_center_crop


# Single-track TrueSkill μ spans roughly this range (matches the pp seed curve
# endpoints in services/duel/rating.py). Used to scale the rating bar.
RATING_MIN = 900.0
RATING_MAX = 3800.0


class DuelProfileCardMixin:

    def generate_duel_card(
        self,
        data: Dict,
        avatar: Optional[Image.Image] = None,
        cover: Optional[Image.Image] = None,
    ) -> BytesIO:
        W, H = 800, 480
        img, draw = self._create_canvas(W, H)

        # ── Header ──────────────────────────────────────────────────────────
        mode_label = "CASUAL" if data.get("mode", "casual") == "casual" else "RANKED"
        self._draw_header(draw, f"PROJECT 1984 — DUEL · {mode_label}", data.get("username", ""), W)

        # ── Hero section ─────────────────────────────────────────────────────
        hero_y = 28
        hero_h = 110
        if cover:
            cropped = cover_center_crop(cover, W, hero_h)
            overlay = Image.new("RGBA", (W, hero_h), (0, 0, 0, 150))
            cropped = Image.alpha_composite(cropped, overlay)
            fade_h = 40
            fade_overlay = Image.new("RGBA", (W, hero_h), (*BG_COLOR[:3], 0))
            fade_mask = Image.new("L", (W, hero_h), 0)
            fade_draw = ImageDraw.Draw(fade_mask)
            for fy in range(fade_h):
                alpha = int(fy / max(fade_h - 1, 1) * 255)
                fade_draw.line([(0, hero_h - fade_h + fy), (W, hero_h - fade_h + fy)], fill=alpha)
            fade_overlay.putalpha(fade_mask)
            cropped = Image.alpha_composite(cropped, fade_overlay)
            img.paste(cropped.convert("RGB"), (0, hero_y))
            draw = ImageDraw.Draw(img)
        else:
            draw.rectangle([(0, hero_y), (W, hero_y + hero_h)], fill=HEADER_BG)

        # Avatar
        avatar_size = 80
        avatar_x = PADDING_X
        avatar_y = hero_y + (hero_h - avatar_size) // 2
        if avatar:
            cropped_av = rounded_rect_crop(avatar, avatar_size, radius=14)
            img.paste(cropped_av, (avatar_x, avatar_y), cropped_av)
            self._aa_rounded_outline(
                img,
                (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
                radius=14, outline=ACCENT_RED, width=2,
            )
        else:
            self._aa_rounded_outline(
                img,
                (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
                radius=14, outline=ACCENT_RED, width=2, fill=(50, 50, 70),
            )
        draw = ImageDraw.Draw(img)

        # Username — positioned first, then flag centered on it
        text_x = avatar_x + avatar_size + 16
        username = data.get("username", "???")
        country = data.get("country", "")
        flag_img = load_flag(country, height=18)

        name_y = hero_y + 14
        username_bbox = draw.textbbox((0, 0), username, font=self.font_big)
        username_h = username_bbox[3] - username_bbox[1]

        if flag_img:
            text_center_y = name_y + username_h // 2
            flag_y = text_center_y - flag_img.height // 2 + 4
            img.paste(flag_img, (text_x, flag_y), flag_img)
            draw = ImageDraw.Draw(img)
            draw.text((text_x + flag_img.width + 8, name_y), username, font=self.font_big, fill=TEXT_PRIMARY)
        else:
            draw.text((text_x, name_y), username, font=self.font_big, fill=TEXT_PRIMARY)

        # DUEL RATING label
        label_y = name_y + username_h + 8
        draw.text((text_x, label_y), "DUEL RATING", font=self.font_label, fill=ACCENT_RED)

        # ── data ──────────────────────────────────────────────────────────────
        mu = float(data.get("mu", 1500.0))
        sigma = float(data.get("sigma", 500.0))
        conservative = float(data.get("conservative", max(0.0, mu - 3.0 * sigma)))
        peak_mu = float(data.get("peak_mu", mu))
        wins = data.get("wins", 0)
        losses = data.get("losses", 0)
        duel_rank = data.get("duel_rank")
        rank_val = f"#{duel_rank}" if duel_rank else "—"
        division = data.get("duel_division") or ""
        placement_left = data.get("placement_matches_left", 0)
        in_placement = placement_left > 0
        # Casual μ has no public meaning (no division, no leaderboard), so we
        # render the calibration treatment for casual too — rating/peak/rank
        # are hidden behind a single "casual — rating not tracked" panel.
        is_casual = (data.get("mode") == "casual")
        games_played = int(data.get("games", 0) or 0)
        hide_rating = in_placement or is_casual

        # ── 4 stat panels ─────────────────────────────────────────────────────
        panels_y = hero_y + hero_h + 10
        panel_h = 54
        gap = 8
        panel_w = (W - 2 * PADDING_X - 3 * gap) // 4

        for i in range(4):
            px = PADDING_X + i * (panel_w + gap)
            self._draw_panel(draw, px, panels_y, panel_w, panel_h)
            cy = panels_y + 6
            ly = panels_y + 30
            cx = px + panel_w // 2

            if i == 0:
                if in_placement:
                    played = 10 - placement_left
                    self._text_center(draw, cx, cy, f"{played} / 10", self.font_row, TEXT_PRIMARY)
                    self._text_center(draw, cx, ly, "MATCHES", self.font_stat_label, TEXT_SECONDARY)
                elif is_casual:
                    self._text_center(draw, cx, cy, f"{games_played}", self.font_row, TEXT_PRIMARY)
                    self._text_center(draw, cx, ly, "MATCHES", self.font_stat_label, TEXT_SECONDARY)
                else:
                    self._text_center(draw, cx, cy, f"{mu:.0f}", self.font_row, TEXT_PRIMARY)
                    self._text_center(draw, cx, ly, "RATING", self.font_stat_label, TEXT_SECONDARY)
            elif i == 1:
                if is_casual:
                    # Casual has no peak μ to brag about — repurpose the cell
                    # for lifetime win-rate, which actually means something.
                    lwr = data.get("lifetime_winrate")
                    val = f"{int(round(lwr * 100))}%" if lwr is not None else "—"
                    self._text_center(draw, cx, cy, val, self.font_row, TEXT_PRIMARY)
                    self._text_center(draw, cx, ly, "WIN RATE", self.font_stat_label, TEXT_SECONDARY)
                elif in_placement:
                    self._text_center(draw, cx, cy, "—", self.font_row, TEXT_SECONDARY)
                    self._text_center(draw, cx, ly, "PEAK", self.font_stat_label, TEXT_SECONDARY)
                else:
                    self._text_center(draw, cx, cy, f"{peak_mu:.0f}", self.font_row, (255, 215, 0))
                    self._text_center(draw, cx, ly, "PEAK", self.font_stat_label, TEXT_SECONDARY)
            elif i == 2:
                w_str, sep_str, l_str = f"{wins}W", " / ", f"{losses}L"
                wb = draw.textbbox((0, 0), w_str, font=self.font_row)
                sb = draw.textbbox((0, 0), sep_str, font=self.font_row)
                lb = draw.textbbox((0, 0), l_str, font=self.font_row)
                total = (wb[2]-wb[0]) + (sb[2]-sb[0]) + (lb[2]-lb[0])
                sx = cx - total // 2
                draw.text((sx, cy), w_str, font=self.font_row, fill=ACCENT_GREEN)
                sx += wb[2] - wb[0]
                draw.text((sx, cy), sep_str, font=self.font_row, fill=TEXT_SECONDARY)
                sx += sb[2] - sb[0]
                draw.text((sx, cy), l_str, font=self.font_row, fill=ACCENT_RED)
                self._text_center(draw, cx, ly, "W/L", self.font_stat_label, TEXT_SECONDARY)
            elif i == 3:
                if is_casual:
                    # Casual has no ladder — show activity instead.
                    g7 = int(data.get("games_7d", 0) or 0)
                    self._text_center(draw, cx, cy, str(g7), self.font_row, TEXT_PRIMARY)
                    self._text_center(draw, cx, ly, "WEEK", self.font_stat_label, TEXT_SECONDARY)
                elif in_placement:
                    self._text_center(draw, cx, cy, "—", self.font_row, TEXT_SECONDARY)
                    self._text_center(draw, cx, ly, "RANK", self.font_stat_label, TEXT_SECONDARY)
                else:
                    self._text_center(draw, cx, cy, rank_val, self.font_row, TEXT_PRIMARY)
                    self._text_center(draw, cx, ly, "RANK", self.font_stat_label, TEXT_SECONDARY)

        # ── Rating distribution / Calibration ────────────────────────────────
        bars_y = panels_y + panel_h + 14

        if in_placement:
            played = 10 - placement_left
            self._draw_calibration_block(draw, bars_y, W, played, 10, mode="ranked")
        elif is_casual:
            # Casual: no calibration goal — show real stats (streaks, recent
            # win-rate, last duel) instead of a meaningless rating bar.
            self._draw_casual_stats_block(draw, bars_y, W, data)
        else:
            self._draw_rating_block(draw, bars_y, W, mu, sigma, conservative, division)

        return self._save(img)

    def _draw_rating_block(self, draw, y: int, W: int, mu: float, sigma: float,
                           conservative: float, division: str) -> None:
        """Single-track TrueSkill view: a μ marker with a ±σ uncertainty band on a
        rating axis, the conservative (μ−3σ) score, and the ranked division."""
        block_h = 150
        x0, x1 = PADDING_X, W - PADDING_X
        self._draw_panel(draw, x0, y, x1 - x0, block_h)

        span = max(RATING_MAX - RATING_MIN, 1.0)

        def to_x(value: float) -> int:
            frac = (max(RATING_MIN, min(value, RATING_MAX)) - RATING_MIN) / span
            return int(x0 + 16 + frac * (x1 - x0 - 32))

        # Bar geometry
        bar_y = y + 54
        bar_h = 16
        bar_left = x0 + 16
        bar_right = x1 - 16

        # Top labels
        draw.text((bar_left, y + 14), "RATING DISTRIBUTION",
                  font=self.font_stat_label, fill=TEXT_SECONDARY)
        div_str = division if division else "—"
        db = draw.textbbox((0, 0), div_str, font=self.font_label)
        draw.text((bar_right - (db[2] - db[0]), y + 13), div_str,
                  font=self.font_label, fill=(255, 215, 0))

        # Track
        draw.rounded_rectangle((bar_left, bar_y, bar_right, bar_y + bar_h),
                               radius=8, fill=(45, 45, 65))

        # ±σ uncertainty band around μ
        band_lo, band_hi = to_x(mu - sigma), to_x(mu + sigma)
        draw.rounded_rectangle((band_lo, bar_y, band_hi, bar_y + bar_h),
                               radius=8, fill=(90, 110, 200))

        # Vertical markers drawn as thin capsules so the ends read as rounded.
        def _marker(cx: int, color) -> None:
            half = 2
            draw.rounded_rectangle(
                (cx - half, bar_y - 6, cx + half, bar_y + bar_h + 6),
                radius=half, fill=color,
            )

        # conservative marker (μ − 3σ) — the ranking score
        _marker(to_x(conservative), ACCENT_GREEN)
        # μ marker
        _marker(to_x(mu), TEXT_PRIMARY)

        # Legend — three inset panels, each a bold value above a smaller label.
        col_blue = (140, 160, 230)
        cols = (
            (f"{mu:.0f}", "RATING", TEXT_PRIMARY),
            (f"{sigma:.0f}", "SPREAD", col_blue),
            (f"{conservative:.0f}", "CONSERVATIVE", ACCENT_GREEN),
        )
        cell_gap = 10
        cell_y = bar_y + bar_h + 14
        cell_h = 56
        cell_w = (bar_right - bar_left - 2 * cell_gap) // 3
        for i, (value, label, color) in enumerate(cols):
            cell_x = bar_left + i * (cell_w + cell_gap)
            self._draw_panel(draw, cell_x, cell_y, cell_w, cell_h, bg=(22, 22, 34))
            cx = cell_x + cell_w // 2
            self._text_center(draw, cx, cell_y + 9, value, self.font_stat_value, color)
            self._text_center(draw, cx, cell_y + 33, label, self.font_stat_label, TEXT_SECONDARY)

    def _draw_calibration_block(self, draw, y: int, W: int, played: int,
                                total: int, *, mode: str = "ranked") -> None:
        cx = W // 2

        # Ranked placement.
        remaining = total - played
        draw.text(
            (cx, y + 20),
            f"Play {remaining} more match{'es' if remaining != 1 else ''}",
            font=self.font_row, fill=TEXT_PRIMARY, anchor="mm",
        )
        draw.text(
            (cx, y + 52), "to unlock your skill stats",
            font=self.font_label, fill=TEXT_SECONDARY, anchor="mm",
        )
        bar_w = W - 2 * PADDING_X
        bar_h = 12
        bar_y = y + 76
        draw.rounded_rectangle(
            (PADDING_X, bar_y, PADDING_X + bar_w, bar_y + bar_h),
            radius=6, fill=(45, 45, 65),
        )
        if played > 0:
            fill_w = max(8, int(bar_w * played / total))
            draw.rounded_rectangle(
                (PADDING_X, bar_y, PADDING_X + fill_w, bar_y + bar_h),
                radius=6, fill=ACCENT_RED,
            )
        draw.text(
            (cx, bar_y + bar_h + 14),
            f"{played} / {total} matches played",
            font=self.font_stat_label, fill=TEXT_SECONDARY, anchor="mm",
        )

    def _draw_casual_stats_block(self, draw, y: int, W: int, data: Dict) -> None:
        """Casual replacement for the rating bar — shows real stats players
        actually care about (record, recent form, streaks, last opponent)."""
        x0, x1 = PADDING_X, W - PADDING_X
        block_h = 150
        self._draw_panel(draw, x0, y, x1 - x0, block_h)

        wins = int(data.get("wins", 0) or 0)
        losses = int(data.get("losses", 0) or 0)
        games = int(data.get("games", 0) or 0)
        cur_streak = int(data.get("current_streak", 0) or 0)
        best_streak = int(data.get("best_streak", 0) or 0)
        recent_wr = data.get("recent_winrate")
        recent_n = int(data.get("recent_count", 0) or 0)
        last = data.get("last_duel")

        # Header line — title left, lifetime W/L on the right.
        draw.text((x0 + 16, y + 14), "CASUAL — RECENT FORM",
                  font=self.font_stat_label, fill=TEXT_SECONDARY)
        wl = f"{wins}W / {losses}L"
        wb = draw.textbbox((0, 0), wl, font=self.font_label)
        draw.text((x1 - 16 - (wb[2] - wb[0]), y + 12), wl,
                  font=self.font_label, fill=TEXT_PRIMARY)

        if games == 0:
            # Empty state — invite them to play a first match.
            draw.text(
                (W // 2, y + 70), "No duels yet — play your first casual match!",
                font=self.font_row, fill=TEXT_PRIMARY, anchor="mm",
            )
            draw.text(
                (W // 2, y + 102), "Casual matches don't change your rating.",
                font=self.font_stat_label, fill=TEXT_SECONDARY, anchor="mm",
            )
            return

        # Three stat cells: WIN RATE (last N) · STREAK · BEST
        cell_y = y + 40
        cell_h = 56
        cell_gap = 10
        cell_w = (x1 - x0 - 32 - 2 * cell_gap) // 3

        wr_txt = f"{int(round(recent_wr * 100))}%" if recent_wr is not None else "—"
        wr_sub = f"LAST {recent_n}" if recent_n else "WIN RATE"

        if cur_streak > 0:
            streak_txt, streak_color = f"W{cur_streak}", ACCENT_GREEN
        elif cur_streak < 0:
            streak_txt, streak_color = f"L{-cur_streak}", ACCENT_RED
        else:
            streak_txt, streak_color = "—", TEXT_SECONDARY

        cells = (
            (wr_txt, wr_sub, TEXT_PRIMARY),
            (streak_txt, "STREAK", streak_color),
            (f"W{best_streak}" if best_streak else "—", "BEST",
             (255, 215, 0) if best_streak else TEXT_SECONDARY),
        )
        for i, (value, label, color) in enumerate(cells):
            cx = x0 + 16 + i * (cell_w + cell_gap)
            self._draw_panel(draw, cx, cell_y, cell_w, cell_h, bg=(22, 22, 34))
            mid = cx + cell_w // 2
            self._text_center(draw, mid, cell_y + 9, value, self.font_stat_value, color)
            self._text_center(draw, mid, cell_y + 33, label, self.font_stat_label, TEXT_SECONDARY)

        # Last duel line — opponent + score + when.
        if last:
            won = bool(last.get("won"))
            opp = str(last.get("opponent") or "?")
            s_self, s_opp = last.get("score") or (0, 0)
            # Plain "Win"/"Loss" — colour already conveys the result and the
            # bundled font has no ✓/✗ glyphs (would render as tofu).
            outcome = "Win" if won else "Loss"
            outcome_color = ACCENT_GREEN if won else ACCENT_RED

            when = ""
            ts = last.get("completed_at")
            if ts is not None:
                try:
                    from datetime import datetime, timezone
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    delta = datetime.now(timezone.utc) - ts
                    days = delta.days
                    if days <= 0:
                        hours = max(1, delta.seconds // 3600)
                        when = f" · {hours}h ago"
                    elif days == 1:
                        when = " · yesterday"
                    elif days < 30:
                        when = f" · {days}d ago"
                    else:
                        when = f" · {days // 30}mo ago"
                except Exception:
                    pass

            line_y = cell_y + cell_h + 12
            label = "LAST"
            lb = draw.textbbox((0, 0), label, font=self.font_stat_label)
            draw.text((x0 + 16, line_y + 4), label,
                      font=self.font_stat_label, fill=TEXT_SECONDARY)

            cursor_x = x0 + 16 + (lb[2] - lb[0]) + 10
            ob = draw.textbbox((0, 0), outcome, font=self.font_label)
            draw.text((cursor_x, line_y), outcome,
                      font=self.font_label, fill=outcome_color)
            cursor_x += (ob[2] - ob[0]) + 8

            rest = f"vs {opp}  ({s_self}–{s_opp}){when}"
            draw.text((cursor_x, line_y), rest,
                      font=self.font_label, fill=TEXT_PRIMARY)

    async def generate_duel_card_async(self, data: Dict) -> BytesIO:
        avatar_url = data.get("avatar_url")
        cover_data = data.get("cover_data")

        avatar = None
        if avatar_url:
            result = await download_image(avatar_url)
            if result and not isinstance(result, Exception):
                avatar = result

        cover = None
        if cover_data:
            try:
                cover = Image.open(BytesIO(cover_data)).convert("RGBA")
            except Exception:
                pass

        return await asyncio.to_thread(self.generate_duel_card, data, avatar, cover)
