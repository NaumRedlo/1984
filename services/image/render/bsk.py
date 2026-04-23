"""BSK profile card renderer."""

import asyncio
from io import BytesIO
from typing import Dict, Optional

from PIL import Image, ImageDraw

from services.image.constants import (
    BG_COLOR, HEADER_BG, TEXT_PRIMARY, TEXT_SECONDARY,
    ACCENT_RED, ACCENT_GREEN, PANEL_BG, PADDING_X,
)
from services.image.utils import download_image, rounded_rect_crop, load_flag, cover_center_crop, _none_coro


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
        cover: Optional[Image.Image] = None,
    ) -> BytesIO:
        W, H = 800, 510
        img, draw = self._create_canvas(W, H)

        # ── Header ──────────────────────────────────────────────────────────
        mode_label = "CASUAL" if data.get("mode", "casual") == "casual" else "RANKED"
        self._draw_header(draw, f"PROJECT 1984 — BEATSKILL · {mode_label}  [BETA]", data.get("username", ""), W)

        # ── Hero section with cover BG ───────────────────────────────────────
        hero_y = 36
        hero_h = 120
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
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle(
                (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
                radius=14, outline=ACCENT_RED, width=2,
            )
        else:
            draw.rounded_rectangle(
                (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
                radius=14, fill=(50, 50, 70), outline=ACCENT_RED, width=2,
            )

        # Username + flag — flag on same line, vertically centered with username
        text_x = avatar_x + avatar_size + 16
        username = data.get("username", "???")
        country = data.get("country", "")
        flag_img = load_flag(country, height=18)

        name_y = hero_y + 16
        username_bbox = draw.textbbox((0, 0), username, font=self.font_big)
        username_h = username_bbox[3] - username_bbox[1]

        if flag_img:
            flag_y = name_y + (username_h - flag_img.height) // 2
            img.paste(flag_img, (text_x, flag_y), flag_img)
            draw = ImageDraw.Draw(img)
            draw.text((text_x + flag_img.width + 8, name_y), username, font=self.font_big, fill=TEXT_PRIMARY)
        else:
            draw.text((text_x, name_y), username, font=self.font_big, fill=TEXT_PRIMARY)

        # BEATSKILL RATING BETA — below username with proper spacing
        label_y = name_y + username_h + 10
        draw.text((text_x, label_y), "BEATSKILL RATING", font=self.font_ru_label, fill=ACCENT_RED)
        bsk_bbox = draw.textbbox((0, 0), "BEATSKILL RATING", font=self.font_ru_label)
        beta_x = text_x + (bsk_bbox[2] - bsk_bbox[0]) + 8
        draw.text((beta_x, label_y + 2), "BETA", font=self.font_stat_label, fill=(160, 160, 180))

        # ── 4 stat panels: BSK POINTS, PEAK BSK, W/L, RANK ──────────────────
        panels_y = hero_y + hero_h + 10
        panel_h = 54
        gap = 8
        panel_w = (W - 2 * PADDING_X - 3 * gap) // 4

        mu_global = data.get("mu_global", 1000.0)
        peak_mu = data.get("peak_mu", 1000.0)
        wins = data.get("wins", 0)
        losses = data.get("losses", 0)
        bsk_rank = data.get("bsk_rank")
        rank_val = f"#{bsk_rank}" if bsk_rank else "—"

        for i in range(4):
            px = PADDING_X + i * (panel_w + gap)
            self._draw_panel(draw, px, panels_y, panel_w, panel_h)
            cy = panels_y + 6
            ly = panels_y + 30
            cx = px + panel_w // 2

            if i == 0:
                self._text_center(draw, cx, cy, f"{mu_global:.0f}", self.font_row, TEXT_PRIMARY)
                self._text_center(draw, cx, ly, "BSK POINTS", self.font_stat_label, TEXT_SECONDARY)
            elif i == 1:
                self._text_center(draw, cx, cy, f"{peak_mu:.0f}", self.font_row, (255, 215, 0))
                self._text_center(draw, cx, ly, "PEAK BSK", self.font_stat_label, TEXT_SECONDARY)
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
                self._text_center(draw, cx, ly, "W / L", self.font_stat_label, TEXT_SECONDARY)
            elif i == 3:
                self._text_center(draw, cx, cy, rank_val, self.font_row, TEXT_PRIMARY)
                self._text_center(draw, cx, ly, "RANK", self.font_stat_label, TEXT_SECONDARY)

        # ── Skill bars ───────────────────────────────────────────────────────
        bars_y = panels_y + panel_h + 14
        bar_row_h = 34
        bar_gap = 8
        label_w = 115
        val_w = 88  # fixed width for value text
        bar_x = PADDING_X + label_w + 10
        bar_w = W - PADDING_X - label_w - val_w - 20
        bar_h = 14

        components = ['aim', 'speed', 'acc', 'cons']
        for i, comp in enumerate(components):
            row_y = bars_y + i * (bar_row_h + bar_gap)
            mu_val = data.get(f"mu_{comp}", 250.0)
            color = SKILL_COLORS[comp]

            # Label — vertically centered with bar
            bar_center_y = row_y + 8 + bar_h // 2
            label_bbox = draw.textbbox((0, 0), SKILL_LABELS[comp], font=self.font_label)
            label_h = label_bbox[3] - label_bbox[1]
            draw.text((PADDING_X, bar_center_y - label_h // 2), SKILL_LABELS[comp], font=self.font_label, fill=TEXT_SECONDARY)

            # Bar bg
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

            # Value — right-aligned, vertically centered with bar
            val_str = f"{mu_val:.0f} / 1000"
            val_bbox = draw.textbbox((0, 0), val_str, font=self.font_label)
            val_h = val_bbox[3] - val_bbox[1]
            val_x = bar_x + bar_w + 10
            draw.text((val_x, bar_center_y - val_h // 2), val_str, font=self.font_label, fill=TEXT_PRIMARY)

        # ── Bottom panel (conservative score) ───────────────────────────────
        bottom_y = bars_y + len(components) * (bar_row_h + bar_gap) + 8
        bottom_h = 50
        conservative = data.get("conservative", 0.0)

        self._draw_panel(draw, PADDING_X, bottom_y, W - 2 * PADDING_X, bottom_h)
        self._text_center(draw, W // 2, bottom_y + 4, f"{conservative:.0f}", self.font_row, ACCENT_GREEN)
        self._text_center(draw, W // 2, bottom_y + 28, "CONSERVATIVE SCORE  (μ - 3σ)", self.font_stat_label, TEXT_SECONDARY)

        return self._save(img)

    async def generate_bsk_card_async(self, data: Dict) -> BytesIO:
        avatar_url = data.get("avatar_url")
        cover_data = data.get("cover_data")

        # Load avatar from URL
        avatar = None
        if avatar_url:
            result = await download_image(avatar_url)
            if result and not isinstance(result, Exception):
                avatar = result

        # Load cover from cached binary data in DB
        cover = None
        if cover_data:
            try:
                cover = Image.open(BytesIO(cover_data)).convert("RGBA")
            except Exception:
                cover = None

        return await asyncio.to_thread(self.generate_bsk_card, data, avatar, cover)
