import asyncio
from io import BytesIO
from typing import Dict, Optional

from PIL import Image, ImageDraw

from services.image.constants import (
    BG_COLOR,
    HEADER_BG,
    ROW_EVEN,
    ROW_ODD,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    ACCENT_RED,
    ACCENT_GREEN,
    PADDING_X,
)
from services.image.utils import (
    download_image,
    cover_center_crop,
    rounded_rect_crop,
)


class CompareCardMixin:
    def generate_compare_card(
        self,
        data: Dict,
        avatar1: Optional[Image.Image] = None,
        cover1: Optional[Image.Image] = None,
        avatar2: Optional[Image.Image] = None,
        cover2: Optional[Image.Image] = None,
    ) -> BytesIO:
        W, H = 800, 580
        img, draw = self._create_canvas(W, H)

        u1 = data.get("user1", {})
        u2 = data.get("user2", {})
        diffs = data.get("diffs", {})

        half_w = W // 2
        header_h = 36
        cover_h = 180
        cover_top = header_h

        draw.rectangle([(0, 0), (W, header_h)], fill=HEADER_BG)
        self._text_center(draw, W // 2, 8, "PROJECT 1984 — COMPARISON", self.font_subtitle, ACCENT_RED)

        if cover1:
            cropped1 = cover_center_crop(cover1, half_w, cover_h)
            overlay1 = Image.new("RGBA", (half_w, cover_h), (0, 0, 0, 100))
            cropped1 = Image.alpha_composite(cropped1, overlay1)
            fade1 = Image.new("L", (half_w, cover_h), 255)
            fade_zone = 80
            for fx in range(fade_zone):
                alpha = 255 - int(fx / fade_zone * 255)
                ImageDraw.Draw(fade1).line([(half_w - fade_zone + fx, 0), (half_w - fade_zone + fx, cover_h)], fill=alpha)
            img.paste(cropped1.convert("RGB"), (0, cover_top), fade1)
        else:
            draw.rectangle([(0, cover_top), (half_w, cover_top + cover_h)], fill=HEADER_BG)

        if cover2:
            cropped2 = cover_center_crop(cover2, half_w, cover_h)
            overlay2 = Image.new("RGBA", (half_w, cover_h), (0, 0, 0, 100))
            cropped2 = Image.alpha_composite(cropped2, overlay2)
            fade2 = Image.new("L", (half_w, cover_h), 255)
            fade_zone = 80
            for fx in range(fade_zone):
                alpha = 255 - int((fade_zone - fx) / fade_zone * 255)
                ImageDraw.Draw(fade2).line([(fx, 0), (fx, cover_h)], fill=alpha)
            img.paste(cropped2.convert("RGB"), (half_w, cover_top), fade2)
        else:
            draw.rectangle([(half_w, cover_top), (W, cover_top + cover_h)], fill=(40, 35, 55))

        draw = ImageDraw.Draw(img)

        vs_y = cover_top + (cover_h - 48) // 2
        self._text_center(draw, W // 2, vs_y, "VS", self.font_vs, TEXT_PRIMARY)

        av_size = 90
        av_y = cover_top + 20
        av1_x = half_w // 2 - av_size // 2
        av2_x = half_w + half_w // 2 - av_size // 2

        if avatar1:
            a1 = rounded_rect_crop(avatar1, av_size, radius=14)
            img.paste(a1, (av1_x, av_y), a1)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((av1_x, av_y, av1_x + av_size, av_y + av_size), radius=14, outline=ACCENT_RED, width=2)
        else:
            draw.rounded_rectangle((av1_x, av_y, av1_x + av_size, av_y + av_size), radius=14, fill=(50, 50, 70), outline=ACCENT_RED, width=2)

        if avatar2:
            a2 = rounded_rect_crop(avatar2, av_size, radius=14)
            img.paste(a2, (av2_x, av_y), a2)
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle((av2_x, av_y, av2_x + av_size, av_y + av_size), radius=14, outline=ACCENT_RED, width=2)
        else:
            draw.rounded_rectangle((av2_x, av_y, av2_x + av_size, av_y + av_size), radius=14, fill=(50, 50, 70), outline=ACCENT_RED, width=2)

        draw = ImageDraw.Draw(img)

        name1 = u1.get("username", "?")
        name2 = u2.get("username", "?")
        name_y = av_y + av_size + 8
        self._text_center(draw, half_w // 2, name_y, name1, self.font_subtitle, TEXT_PRIMARY)
        self._text_center(draw, half_w + half_w // 2, name_y, name2, self.font_subtitle, TEXT_PRIMARY)

        metrics = [
            ("PP", f"{u1.get('pp', 0):,}", f"{u2.get('pp', 0):,}", diffs.get("pp", 0), False),
            ("Rank", f"#{u1.get('rank', 0):,}", f"#{u2.get('rank', 0):,}", diffs.get("rank", 0), True),
            ("Accuracy", f"{u1.get('accuracy', 0):.2f}%", f"{u2.get('accuracy', 0):.2f}%", diffs.get("accuracy", 0), False),
            ("Play Count", f"{u1.get('play_count', 0):,}", f"{u2.get('play_count', 0):,}", diffs.get("play_count", 0), False),
            ("Play Time", str(u1.get("play_time", "—")), str(u2.get("play_time", "—")), diffs.get("play_time", 0), False),
            ("Ranked Score", f"{u1.get('ranked_score', 0):,}", f"{u2.get('ranked_score', 0):,}", diffs.get("ranked_score", 0), False),
        ]

        y = cover_top + cover_h + 10
        row_h = 58
        col_left = PADDING_X + 10
        col_center = W // 2
        col_right = W - PADDING_X - 10

        for i, (metric_name, v1, v2, diff_val, invert) in enumerate(metrics):
            ry = y + i * row_h
            row_bg = ROW_EVEN if i % 2 == 0 else ROW_ODD
            draw.rectangle([(0, ry), (W, ry + row_h)], fill=row_bg)

            ty = ry + (row_h - 22) // 2

            win1 = False
            win2 = False
            if diff_val != 0:
                positive = diff_val > 0
                if invert:
                    positive = not positive
                win1 = positive
                win2 = not positive

            c1 = ACCENT_GREEN if win1 else (ACCENT_RED if win2 else TEXT_PRIMARY)
            c2 = ACCENT_GREEN if win2 else (ACCENT_RED if win1 else TEXT_PRIMARY)

            draw.text((col_left, ty), v1, font=self.font_row, fill=c1)
            self._text_center(draw, col_center, ty, metric_name, self.font_label, TEXT_SECONDARY)
            self._text_right(draw, col_right, ty, v2, self.font_row, c2)

        return self._save(img)

    async def generate_compare_card_async(self, data: Dict) -> BytesIO:
        u1 = data.get("user1", {})
        u2 = data.get("user2", {})

        results = await asyncio.gather(
            download_image(u1.get("avatar_url")),
            download_image(u1.get("cover_url")),
            download_image(u2.get("avatar_url")),
            download_image(u2.get("cover_url")),
            return_exceptions=True,
        )
        imgs = [r if not isinstance(r, Exception) else None for r in results]
        return await asyncio.to_thread(self.generate_compare_card, data, imgs[0], imgs[1], imgs[2], imgs[3])

