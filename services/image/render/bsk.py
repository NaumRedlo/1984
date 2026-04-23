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

        # Username + flag — flag vertically centered on username text
        text_x = avatar_x + avatar_size + 16
        username = data.get("username", "???")
        country = data.get("country", "")
        flag_img = load_flag(country, height=20)

        name_y = hero_y + 18
        username_bbox = draw.textbbox((0, 0), username, font=self.font_big)
        username_h = username_bbox[3] - username_bbox[1]

        if flag_img:
            flag_y = name_y + (username_h - flag_img.height) // 2 + 2
            img.paste(flag_img, (text_x, flag_y), flag_img)
            draw = ImageDraw.Draw(img)
            draw.text((text_x + flag_img.width + 8, name_y), username, font=self.font_big, fill=TEXT_PRIMARY)
        else:
            draw.text((text_x, name_y), username, font=self.font_big, fill=TEXT_PRIMARY)

        # BEATSKILL RATING + BETA
        label_y = name_y + username_h + 8
        draw.text((text_x, label_y), "BEATSKILL RATING", font=self.font_ru_label, fill=ACCENT_RED)
        bsk_bbox = draw.textbbox((0, 0), "BEATSKILL RATING", font=self.font_ru_label)
        beta_x = text_x + (bsk_bbox[2] - bsk_bbox[0]) + 8
        draw.text((beta_x, label_y + 2), "BETA", font=self.font_stat_label, fill=(160, 160, 180))

        # ── 3 stat panels (BSK POINTS, W/L, RANK) ───────────────────────────
        panels_y = hero_y + hero_h + 10
        panel_h = 54
        gap = 8
        panel_w = (W - 2 * PADDING_X - 2 * gap) // 3

        mu_global = data.get("mu_global", 1000.0)
        wins = data.get("wins", 0)
        losses = data.get("losses", 0)
        placement_left = data.get("placement_matches_left", 0)
        bsk_rank = data.get("bsk_rank")
        rank_val = f"#{bsk_rank}" if bsk_rank else "—"

        stat_panels = [
            (f"{mu_global:.0f}", "BSK POINTS"),
            (None, "W / L"),   # special W/L rendering
            (rank_val, "RANK"),
        ]

        for i, (val, label) in enumerate(stat_panels):
            px = PADDING_X + i * (panel_w + gap)
            self._draw_panel(draw, px, panels_y, panel_w, panel_h)

            if label == "W / L":
                w_str = f"{wins}W"
                sep_str = " / "
                l_str = f"{losses}L"
                w_bbox = draw.textbbox((0, 0), w_str, font=self.font_row)
                sep_bbox = draw.textbbox((0, 0), sep_str, font=self.font_row)
                l_bbox = draw.textbbox((0, 0), l_str, font=self.font_row)
                total_w = (w_bbox[2] - w_bbox[0]) + (sep_bbox[2] - sep_bbox[0]) + (l_bbox[2] - l_bbox[0])
                cx = px + panel_w // 2
                sx = cx - total_w // 2
                ty = panels_y + 6
                draw.text((sx, ty), w_str, font=self.font_row, fill=ACCENT_GREEN)
                sx += w_bbox[2] - w_bbox[0]
                draw.text((sx, ty), sep_str, font=self.font_row, fill=TEXT_SECONDARY)
                sx += sep_bbox[2] - sep_bbox[0]
                draw.text((sx, ty), l_str, font=self.font_row, fill=ACCENT_RED)
            else:
                self._text_center(draw, px + panel_w // 2, panels_y + 6, val, self.font_row, TEXT_PRIMARY)

            self._text_center(draw, px + panel_w // 2, panels_y + 30, label, self.font_stat_label, TEXT_SECONDARY)

        # ── Skill bars ───────────────────────────────────────────────────────
        bars_y = panels_y + panel_h + 14
        bar_row_h = 34
        bar_gap = 8
        label_w = 115
        bar_x = PADDING_X + label_w + 10
        bar_end = W - PADDING_X
        bar_w = bar_end - bar_x - 90  # leave 90px for value text
        bar_h = 14

        components = ['aim', 'speed', 'acc', 'cons']
        for i, comp in enumerate(components):
            row_y = bars_y + i * (bar_row_h + bar_gap)
            mu_val = data.get(f"mu_{comp}", 250.0)
            color = SKILL_COLORS[comp]

            draw.text((PADDING_X, row_y + 9), SKILL_LABELS[comp], font=self.font_label, fill=TEXT_SECONDARY)

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

            # Value — left-aligned right after bar
            val_str = f"{mu_val:.0f} / 1000"
            draw.text((bar_x + bar_w + 10, row_y + 9), val_str, font=self.font_label, fill=TEXT_PRIMARY)

        # ── Bottom panel (conservative + peak) ──────────────────────────────
        bottom_y = bars_y + len(components) * (bar_row_h + bar_gap) + 8
        bottom_h = 50
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
        avatar_url = data.get("avatar_url")
        cover_url = data.get("cover_url")

        results = await asyncio.gather(
            download_image(avatar_url) if avatar_url else _none_coro(),
            download_image(cover_url) if cover_url else _none_coro(),
            return_exceptions=True,
        )
        avatar = results[0] if results[0] and not isinstance(results[0], Exception) else None
        cover = results[1] if results[1] and not isinstance(results[1], Exception) else None

        return await asyncio.to_thread(self.generate_bsk_card, data, avatar, cover)
