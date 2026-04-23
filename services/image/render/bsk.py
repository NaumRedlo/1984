"""BSK profile card renderer."""

import asyncio
from io import BytesIO
from typing import Dict, Optional

from PIL import Image, ImageDraw

from services.image.constants import (
    BG_COLOR, HEADER_BG, TEXT_PRIMARY, TEXT_SECONDARY,
    ACCENT_RED, ACCENT_GREEN, PANEL_BG, PADDING_X,
)
from services.image.utils import download_image, rounded_rect_crop, load_flag


# Skill component colours
SKILL_COLORS = {
    'aim':   (200, 80,  80),
    'speed': (80,  140, 220),
    'acc':   (80,  200, 120),
    'cons':  (200, 180, 60),
}

SKILL_LABELS = {
    'aim':   'AIM',
    'speed': 'SPEED',
    'acc':   'ACCURACY',
    'cons':  'CONSISTENCY',
}


class BskCardMixin:

    def generate_bsk_card(
        self,
        data: Dict,
        avatar: Optional[Image.Image] = None,
    ) -> BytesIO:
        W, H = 800, 520
        img, draw = self._create_canvas(W, H)

        # ── Header ──────────────────────────────────────────────────────────
        mode_label = "CASUAL" if data.get("mode", "casual") == "casual" else "RANKED"
        self._draw_header(draw, f"PROJECT 1984 — BEATSKILL · {mode_label}", data.get("username", ""), W)

        # ── Hero section (avatar + username + flag) ──────────────────────────
        hero_y = 36
        hero_h = 110
        draw.rectangle([(0, hero_y), (W, hero_y + hero_h)], fill=HEADER_BG)

        avatar_size = 72
        avatar_x = PADDING_X
        avatar_y = hero_y + (hero_h - avatar_size) // 2
        if avatar:
            cropped = rounded_rect_crop(avatar, avatar_size, radius=12)
            img.paste(cropped, (avatar_x, avatar_y), cropped)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle(
                (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
                radius=12, outline=ACCENT_RED, width=2,
            )
        else:
            draw.rounded_rectangle(
                (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
                radius=12, fill=(50, 50, 70), outline=ACCENT_RED, width=2,
            )

        text_x = avatar_x + avatar_size + 16
        username = data.get("username", "???")
        country = data.get("country", "")
        flag_img = load_flag(country, height=20)

        name_y = hero_y + 22
        if flag_img:
            img.paste(flag_img, (text_x, name_y + 4), flag_img)
            draw = ImageDraw.Draw(img)
            draw.text((text_x + flag_img.width + 8, name_y), username, font=self.font_big, fill=TEXT_PRIMARY)
        else:
            draw.text((text_x, name_y), username, font=self.font_big, fill=TEXT_PRIMARY)

        draw.text((text_x, name_y + 44), "BEATSKILL RATING", font=self.font_stat_label, fill=ACCENT_RED)

        # ── 4 stat panels ────────────────────────────────────────────────────
        panels_y = hero_y + hero_h + 10
        panel_h = 54
        gap = 8
        panel_w = (W - 2 * PADDING_X - 3 * gap) // 4

        mu_global = data.get("mu_global", 1000.0)
        wins = data.get("wins", 0)
        losses = data.get("losses", 0)
        placement_left = data.get("placement_matches_left", 0)

        if placement_left > 0:
            status_val = f"{placement_left} left"
            status_label = "PLACEMENT"
        else:
            status_val = "RANKED"
            status_label = "STATUS"

        stat_panels = [
            (f"{mu_global:.0f}", "BSK SCORE"),
            (mode_label, "MODE"),
            (f"{wins}W / {losses}L", "W / L"),
            (status_val, status_label),
        ]

        for i, (val, label) in enumerate(stat_panels):
            px = PADDING_X + i * (panel_w + gap)
            self._draw_panel(draw, px, panels_y, panel_w, panel_h)
            self._text_center(draw, px + panel_w // 2, panels_y + 6, val, self.font_row, TEXT_PRIMARY)
            self._text_center(draw, px + panel_w // 2, panels_y + 30, label, self.font_stat_label, TEXT_SECONDARY)

        # ── Skill bars ───────────────────────────────────────────────────────
        bars_y = panels_y + panel_h + 16
        bar_row_h = 38
        bar_gap = 8
        label_w = 120
        value_w = 90
        bar_x = PADDING_X + label_w + 10
        bar_w = W - PADDING_X - label_w - value_w - 20
        bar_h = 14

        components = ['aim', 'speed', 'acc', 'cons']
        for i, comp in enumerate(components):
            row_y = bars_y + i * (bar_row_h + bar_gap)
            mu_val = data.get(f"mu_{comp}", 250.0)
            color = SKILL_COLORS[comp]
            label = SKILL_LABELS[comp]

            # Label
            draw.text((PADDING_X, row_y + 10), label, font=self.font_label, fill=TEXT_SECONDARY)

            # Bar background
            draw.rounded_rectangle(
                (bar_x, row_y + 8, bar_x + bar_w, row_y + 8 + bar_h),
                radius=7, fill=(45, 45, 65),
            )
            # Bar fill
            fill_w = max(8, int(bar_w * min(mu_val / 1000.0, 1.0)))
            draw.rounded_rectangle(
                (bar_x, row_y + 8, bar_x + fill_w, row_y + 8 + bar_h),
                radius=7, fill=color,
            )

            # Value
            val_str = f"{mu_val:.0f} / 1000"
            self._text_right(draw, W - PADDING_X, row_y + 10, val_str, self.font_label, TEXT_PRIMARY)

        # ── Bottom panel (conservative + peak) ──────────────────────────────
        bottom_y = bars_y + len(components) * (bar_row_h + bar_gap) + 8
        bottom_h = 52
        half = (W - 2 * PADDING_X - gap) // 2

        conservative = data.get("conservative", 0.0)
        peak_mu = data.get("peak_mu", 1000.0)

        self._draw_panel(draw, PADDING_X, bottom_y, half, bottom_h)
        self._text_center(draw, PADDING_X + half // 2, bottom_y + 4, f"{conservative:.0f}", self.font_row, ACCENT_GREEN)
        self._text_center(draw, PADDING_X + half // 2, bottom_y + 28, "CONSERVATIVE SCORE", self.font_stat_label, TEXT_SECONDARY)

        self._draw_panel(draw, PADDING_X + half + gap, bottom_y, half, bottom_h)
        self._text_center(draw, PADDING_X + half + gap + half // 2, bottom_y + 4, f"{peak_mu:.0f}", self.font_row, (255, 215, 0))
        self._text_center(draw, PADDING_X + half + gap + half // 2, bottom_y + 28, "PEAK BSK", self.font_stat_label, TEXT_SECONDARY)

        return self._save(img)

    async def generate_bsk_card_async(self, data: Dict) -> BytesIO:
        avatar = None
        avatar_url = data.get("avatar_url")
        if avatar_url:
            result = await download_image(avatar_url)
            if not isinstance(result, Exception):
                avatar = result
        return await asyncio.to_thread(self.generate_bsk_card, data, avatar)
