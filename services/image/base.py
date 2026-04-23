"""
BaseCardRenderer — shared drawing primitives for all card types.
"""

from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from utils.logger import get_logger
from services.image.constants import (
    BG_COLOR, HEADER_BG, TEXT_PRIMARY, TEXT_SECONDARY,
    ACCENT_RED, PANEL_BG, MOD_COLORS,
    PADDING_X,
    TORUS_BOLD, TORUS_SEMI, TORUS_REG, HUNINN,
)
from services.image.utils import _find_font

logger = get_logger("services.image_gen")


class BaseCardRenderer:
    """Shared drawing primitives for all card types."""

    def __init__(self):
        bold = _find_font(TORUS_BOLD)
        semi = _find_font(TORUS_SEMI)
        reg = _find_font(TORUS_REG)

        huninn = _find_font(HUNINN)

        if bold:
            self.font_title = ImageFont.truetype(bold, 28)
            self.font_big = ImageFont.truetype(bold, 34)
            self.font_subtitle = ImageFont.truetype(semi or bold, 20)
            self.font_row = ImageFont.truetype(bold, 22)
            self.font_grade = ImageFont.truetype(bold, 40)
            self.font_label = ImageFont.truetype(semi or bold, 18)
            self.font_small = ImageFont.truetype(reg or bold, 16)
            self.font_vs = ImageFont.truetype(bold, 48)
            self.font_stat_value = ImageFont.truetype(bold, 26)
            self.font_stat_label = ImageFont.truetype(semi or bold, 14)
        else:
            logger.warning("No TTF fonts found, falling back to default bitmap font")
            default = ImageFont.load_default()
            self.font_title = default
            self.font_big = default
            self.font_subtitle = default
            self.font_row = default
            self.font_grade = default
            self.font_label = default
            self.font_small = default
            self.font_vs = default
            self.font_stat_value = default
            self.font_stat_label = default

        # Huninn (Cyrillic-capable) for labels and section titles
        if huninn:
            self.font_ru_section = ImageFont.truetype(huninn, 20)
            self.font_ru_label = ImageFont.truetype(huninn, 18)
            self.font_ru_stat_label = ImageFont.truetype(huninn, 14)
        else:
            self.font_ru_section = self.font_subtitle
            self.font_ru_label = self.font_label
            self.font_ru_stat_label = self.font_stat_label

    # Canvas

    def _create_canvas(self, w: int, h: int):
        img = Image.new("RGB", (w, h), BG_COLOR)
        draw = ImageDraw.Draw(img)
        return img, draw

    # Header

    def _draw_header(self, draw: ImageDraw.Draw, title: str, subtitle: str, w: int):
        """Compact 36px header: title centered, username right-aligned gray."""
        h = 36
        draw.rectangle([(0, 0), (w, h)], fill=HEADER_BG)
        self._text_center(draw, w // 2, 8, title, self.font_subtitle, ACCENT_RED)
        if subtitle:
            self._text_right(draw, w - PADDING_X, 10, subtitle, self.font_small, TEXT_SECONDARY)
        draw.line([(0, h - 2), (w, h - 2)], fill=ACCENT_RED, width=2)

    # Footer

    def _draw_footer(self, draw: ImageDraw.Draw, img: Image.Image, text: str, y: int, w: int):
        draw.line([(0, y), (w, y)], fill=ACCENT_RED, width=1)
        draw.text((PADDING_X, y + 6), text, font=self.font_small, fill=TEXT_SECONDARY)

    # Separator

    def _draw_separator(self, draw: ImageDraw.Draw, y: int, w: int):
        draw.line([(PADDING_X, y), (w - PADDING_X, y)], fill=ACCENT_RED, width=1)

    # Key-Value row

    def _draw_kv_row(
        self, draw: ImageDraw.Draw, y: int,
        label: str, value: str,
        label_font=None, value_font=None,
        label_fill=None, value_fill=None,
        x: int = PADDING_X,
    ):
        lf = label_font or self.font_ru_label
        vf = value_font or self.font_row
        lc = label_fill or TEXT_SECONDARY
        vc = value_fill or TEXT_PRIMARY
        draw.text((x, y), f"{label}:", font=lf, fill=lc)
        bbox = draw.textbbox((0, 0), f"{label}:", font=lf)
        lw = bbox[2] - bbox[0]
        draw.text((x + lw + 8, y), value, font=vf, fill=vc)

    # Section title

    def _draw_section_title(self, draw: ImageDraw.Draw, y: int, text: str):
        draw.text((PADDING_X, y), text, font=self.font_ru_section, fill=ACCENT_RED)

    # Right-aligned text

    def _text_right(self, draw: ImageDraw.Draw, x_right: int, y: int, text: str, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text((x_right - tw, y), text, font=font, fill=fill)

    # Center-aligned text

    def _text_center(self, draw: ImageDraw.Draw, cx: int, y: int, text: str, font, fill):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text((cx - tw // 2, y), text, font=font, fill=fill)

    # Panel (rounded rect bg)

    def _draw_panel(self, draw: ImageDraw.Draw, x: int, y: int, w: int, h: int, bg=PANEL_BG):
        draw.rounded_rectangle((x, y, x + w, y + h), radius=8, fill=bg)

    # Stat cell (value on top, label below)

    def _draw_stat_cell(self, draw: ImageDraw.Draw, cx: int, y: int, value: str, label: str):
        self._text_center(draw, cx, y, value, self.font_stat_value, TEXT_PRIMARY)
        self._text_center(draw, cx, y + 30, label, self.font_ru_stat_label, TEXT_SECONDARY)

    def _draw_mini_badge(self, draw: ImageDraw.Draw, x: int, y: int, w: int, h: int, value: str, label: str):
        self._draw_panel(draw, x, y, w, h)
        self._text_center(draw, x + w // 2, y + 2, value, self.font_small, TEXT_PRIMARY)
        self._text_center(draw, x + w // 2, y + h - 12, label, self.font_stat_label, TEXT_SECONDARY)

    # Mod badges (colored rounded rects with white text)

    def _draw_mod_badges(self, img: Image.Image, draw: ImageDraw.Draw, x: int, y: int, mods, badge_h: int = 16, badge_r: int = 10, spacing: int = 4) -> ImageDraw.Draw:
        """Draw colored mod badges starting at (x, y). Returns updated draw.
        `mods` can be a comma-separated string, a list of strings, or a list of dicts with 'acronym'.
        """
        if not mods:
            return draw
        # Normalize to list of mod name strings
        if isinstance(mods, str):
            mod_list = [m.strip() for m in mods.replace("+", "").split(",") if m.strip()]
        elif isinstance(mods, list):
            mod_list = []
            for m in mods:
                if isinstance(m, str):
                    mod_list.append(m.strip())
                elif isinstance(m, dict):
                    mod_list.append(m.get("acronym", ""))
            mod_list = [m for m in mod_list if m]
        else:
            return draw
        # Filter CL (Classic) — auto-added by lazer, visual noise
        mod_list = [m for m in mod_list if m != "CL"]
        cur_x = x
        for mod_name in mod_list:
            mc = MOD_COLORS.get(mod_name, (100, 100, 120))
            mb = draw.textbbox((0, 0), mod_name, font=self.font_stat_label)
            mw = mb[2] - mb[0] + 10
            draw.rounded_rectangle((cur_x, y, cur_x + mw, y + badge_h), radius=badge_r, fill=mc)
            self._text_center(draw, cur_x + mw // 2, y + 1, mod_name, self.font_stat_label, (255, 255, 255))
            cur_x += mw + spacing
        return draw

    # Save helper

    @staticmethod
    def _save(img: Image.Image) -> BytesIO:
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf
