"""BSK division change card renderer."""
import asyncio
from io import BytesIO
from typing import Dict, Optional

from PIL import Image, ImageDraw, ImageFilter

from services.image.constants import (
    BG_COLOR, HEADER_BG, TEXT_PRIMARY, TEXT_SECONDARY,
    ACCENT_RED, ACCENT_GREEN, PADDING_X, BSK_DIVISION_COLORS, BSK_DIVISION_GRADIENTS,
)
from services.image.utils import download_image, rounded_rect_crop, load_flag, cover_center_crop


def _draw_gradient_text(draw, x: int, y: int, text: str, font, color_start: tuple, color_end: tuple) -> int:
    """Draw text with left-to-right gradient. Returns total width drawn."""
    chars = list(text)
    n = max(len(chars) - 1, 1)
    cx = x
    for i, ch in enumerate(chars):
        t = i / n
        r = int(color_start[0] + (color_end[0] - color_start[0]) * t)
        g = int(color_start[1] + (color_end[1] - color_start[1]) * t)
        b = int(color_start[2] + (color_end[2] - color_start[2]) * t)
        draw.text((cx, y), ch, font=font, fill=(r, g, b))
        bbox = draw.textbbox((0, 0), ch, font=font)
        cx += bbox[2] - bbox[0]
    return cx - x


class BskDivisionCardMixin:

    def generate_bsk_division_card(
        self,
        data: Dict,
        avatar: Optional[Image.Image] = None,
        cover: Optional[Image.Image] = None,
    ) -> BytesIO:
        HEADER_H = 28
        W, H = 800, 268
        hero_h = 130
        bottom_h = H - hero_h - HEADER_H
        half_w = W // 2

        new_div = data.get("new_div", "")
        new_div_base = new_div.split()[0] if new_div else ""
        new_color = BSK_DIVISION_COLORS.get(new_div_base, TEXT_PRIMARY)
        grad_pair = BSK_DIVISION_GRADIENTS.get(new_div_base, (new_color, TEXT_PRIMARY))
        is_promotion = data.get("is_promotion", True)
        arrow_color = ACCENT_GREEN if is_promotion else ACCENT_RED
        occurred_at = data.get("occurred_at", "")

        img = Image.new("RGB", (W, H), BG_COLOR)
        draw = ImageDraw.Draw(img)

        # ── Header bar ───────────────────────────────────────────────────────
        draw.rectangle([(0, 0), (W, HEADER_H)], fill=(18, 18, 28))
        header_text = "PROJECT 1984 — RANK UP" if is_promotion else "PROJECT 1984 — RANK DOWN"
        hdr_bbox = draw.textbbox((0, 0), header_text, font=self.font_stat_label)
        hdr_h = hdr_bbox[3] - hdr_bbox[1]
        draw.text(
            (PADDING_X, (HEADER_H - hdr_h) // 2),
            header_text, font=self.font_stat_label, fill=arrow_color,
        )

        # Date — top right inside header, plain text
        if occurred_at:
            date_str = str(occurred_at)
            d_bbox = draw.textbbox((0, 0), date_str, font=self.font_stat_label)
            d_w = d_bbox[2] - d_bbox[0]
            d_h = d_bbox[3] - d_bbox[1]
            draw.text(
                (W - PADDING_X - d_w, (HEADER_H - d_h) // 2),
                date_str, font=self.font_stat_label, fill=TEXT_SECONDARY,
            )

        # ── Hero: cover background ───────────────────────────────────────────
        hero_top = HEADER_H
        if cover:
            hero_crop = cover_center_crop(cover, W, hero_h)
            overlay = Image.new("RGBA", (W, hero_h), (0, 0, 0, 155))
            hero_crop = Image.alpha_composite(hero_crop, overlay)
            img.paste(hero_crop.convert("RGB"), (0, hero_top))
        else:
            draw.rectangle([(0, hero_top), (W, hero_top + hero_h)], fill=HEADER_BG)

        # ── Bottom: blurred cover split ──────────────────────────────────────
        bottom_top = hero_top + hero_h
        if cover:
            bottom_crop = cover_center_crop(cover, W, bottom_h)
            bottom_crop = bottom_crop.convert("RGB").filter(ImageFilter.GaussianBlur(radius=14))
            tint = Image.new("RGBA", (W, bottom_h), (*new_color, 35))
            dark = Image.new("RGBA", (W, bottom_h), (0, 0, 0, 185))
            bottom_rgba = bottom_crop.convert("RGBA")
            bottom_rgba = Image.alpha_composite(bottom_rgba, dark)
            bottom_rgba = Image.alpha_composite(bottom_rgba, tint)
            img.paste(bottom_rgba.convert("RGB"), (0, bottom_top))
        else:
            draw.rectangle([(0, bottom_top), (W, H)], fill=(22, 22, 34))

        draw = ImageDraw.Draw(img)

        # Divider lines
        draw.line([(0, bottom_top), (W, bottom_top)], fill=(60, 60, 80), width=1)
        draw.line([(half_w, bottom_top), (half_w, H)], fill=(60, 60, 80), width=1)

        # ── Avatar ───────────────────────────────────────────────────────────
        avatar_size = 76
        avatar_x = PADDING_X
        avatar_y = hero_top + (hero_h - avatar_size) // 2
        if avatar:
            cropped_av = rounded_rect_crop(avatar, avatar_size, radius=12)
            img.paste(cropped_av, (avatar_x, avatar_y), cropped_av)
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

        # ── Username + flag ──────────────────────────────────────────────────
        text_x = avatar_x + avatar_size + 16
        username = data.get("username", "???")
        country = data.get("country", "")
        flag_img = load_flag(country, height=18)

        name_bbox = draw.textbbox((0, 0), username, font=self.font_big)
        name_h = name_bbox[3] - name_bbox[1]
        name_y = avatar_y + (avatar_size - name_h) // 2 - 14

        if flag_img:
            flag_cy = name_y + name_h // 2
            flag_y = flag_cy - flag_img.height // 2 + 5
            img.paste(flag_img, (text_x, flag_y), flag_img)
            draw = ImageDraw.Draw(img)
            draw.text((text_x + flag_img.width + 8, name_y), username, font=self.font_big, fill=TEXT_PRIMARY)
        else:
            draw.text((text_x, name_y), username, font=self.font_big, fill=TEXT_PRIMARY)

        # ── New division (gradient) + triangle arrow ─────────────────────────
        div_y = name_y + name_h + 14

        tri_size = 9
        tri_cy = div_y + 7
        if is_promotion:
            pts = [(text_x, tri_cy + tri_size),
                   (text_x + tri_size, tri_cy + tri_size),
                   (text_x + tri_size // 2, tri_cy - tri_size // 2)]
        else:
            pts = [(text_x, tri_cy - tri_size // 2),
                   (text_x + tri_size, tri_cy - tri_size // 2),
                   (text_x + tri_size // 2, tri_cy + tri_size)]
        draw.polygon(pts, fill=arrow_color)

        new_x = text_x + tri_size + 10
        _draw_gradient_text(draw, new_x, div_y - 2, new_div, self.font_row, grad_pair[0], grad_pair[1])

        # ── Mode badge — top right of hero, text centered inside pill ────────
        mode = data.get("mode", "ranked").upper()
        mode_bbox = draw.textbbox((0, 0), mode, font=self.font_label)
        mode_w = mode_bbox[2] - mode_bbox[0]
        mode_h = mode_bbox[3] - mode_bbox[1]
        mode_pad_x, mode_pad_y = 12, 7
        badge_w = mode_w + mode_pad_x * 2
        badge_h = mode_h + mode_pad_y * 2
        badge_x = W - PADDING_X - badge_w
        badge_y = hero_top + PADDING_X // 2
        draw.rounded_rectangle(
            (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
            radius=badge_h // 2, fill=(*new_color, 60) if len(new_color) == 3 else new_color,
        )
        draw.rounded_rectangle(
            (badge_x, badge_y, badge_x + badge_w, badge_y + badge_h),
            radius=badge_h // 2, outline=new_color, width=1,
        )
        draw.text(
            (badge_x + (badge_w - mode_w) // 2, badge_y + mode_pad_y),
            mode, font=self.font_label, fill=TEXT_PRIMARY,
        )

        # ── Bottom LEFT: division progress ───────────────────────────────────
        # Dots span from PADDING_X to half_w - PADDING_X
        left_panel_w = half_w - PADDING_X * 2
        dot_labels = ["III", "II", "I"]
        dot_count = 3
        dot_r = 6
        dot_y = bottom_top + bottom_h // 2 - 4
        dot_start_x = PADDING_X + 10
        dot_end_x = half_w - PADDING_X - 10
        dot_spacing = (dot_end_x - dot_start_x) // (dot_count - 1)

        div_suffix = new_div.split()[-1] if new_div else "III"
        suffix_idx = {"III": 0, "II": 1, "I": 2}.get(div_suffix, 0)

        for i in range(dot_count):
            dx = dot_start_x + i * dot_spacing
            filled = i <= suffix_idx
            dot_color = grad_pair[0] if filled else (60, 60, 80)

            if i < dot_count - 1:
                next_dx = dot_start_x + (i + 1) * dot_spacing
                line_color = grad_pair[0] if (i + 1) <= suffix_idx else (60, 60, 80)
                draw.line([(dx + dot_r, dot_y), (next_dx - dot_r, dot_y)], fill=line_color, width=2)

            draw.ellipse((dx - dot_r, dot_y - dot_r, dx + dot_r, dot_y + dot_r), fill=dot_color)
            if filled:
                draw.ellipse(
                    (dx - dot_r + 2, dot_y - dot_r + 2, dx + dot_r - 2, dot_y + dot_r - 2),
                    fill=grad_pair[0],
                )

            lbl = dot_labels[i]
            lbl_bbox = draw.textbbox((0, 0), lbl, font=self.font_stat_label)
            lbl_w = lbl_bbox[2] - lbl_bbox[0]
            draw.text(
                (dx - lbl_w // 2, dot_y + dot_r + 6),
                lbl, font=self.font_stat_label,
                fill=grad_pair[0] if filled else TEXT_SECONDARY,
            )

        # Division name label — centered over the full dots span
        dots_center_x = (dot_start_x + dot_end_x) // 2
        div_label_bbox = draw.textbbox((0, 0), new_div_base.upper(), font=self.font_stat_label)
        div_label_w = div_label_bbox[2] - div_label_bbox[0]
        draw.text(
            (dots_center_x - div_label_w // 2, dot_y - dot_r - 20),
            new_div_base.upper(),
            font=self.font_stat_label,
            fill=(*grad_pair[0],),
        )

        # ── Bottom RIGHT: BSK points ──────────────────────────────────────────
        right_x = half_w + PADDING_X
        bsk_points = data.get("bsk_points")

        if bsk_points is not None:
            label_str = "Current BSK"
            val_str = f"{bsk_points:.0f}"
            lbl_bbox = draw.textbbox((0, 0), label_str, font=self.font_stat_label)
            val_bbox = draw.textbbox((0, 0), val_str, font=self.font_row)
            total_h = (lbl_bbox[3] - lbl_bbox[1]) + 4 + (val_bbox[3] - val_bbox[1])
            block_y = bottom_top + (bottom_h - total_h) // 2
            draw.text((right_x, block_y), label_str, font=self.font_stat_label, fill=TEXT_SECONDARY)
            block_y += (lbl_bbox[3] - lbl_bbox[1]) + 4
            _draw_gradient_text(draw, right_x, block_y, val_str, self.font_row, grad_pair[0], grad_pair[1])

        # Accent line at bottom — green for promotion, red for demotion
        draw.rectangle([(0, H - 3), (W, H)], fill=arrow_color)

        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf

    async def generate_bsk_division_card_async(self, data: Dict) -> BytesIO:
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

        return await asyncio.to_thread(self.generate_bsk_division_card, data, avatar, cover)
