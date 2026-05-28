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
from services.image.utils import _find_font, load_mod_icon

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
        """Compact 28px header: title left-aligned in accent red, subtitle right-aligned gray."""
        h = 28
        draw.rectangle([(0, 0), (w, h)], fill=(18, 18, 28))
        title_bbox = draw.textbbox((0, 0), title, font=self.font_stat_label)
        title_h = title_bbox[3] - title_bbox[1]
        draw.text((PADDING_X, (h - title_h) // 2), title, font=self.font_stat_label, fill=ACCENT_RED)
        if subtitle:
            sub_bbox = draw.textbbox((0, 0), subtitle, font=self.font_stat_label)
            sub_w = sub_bbox[2] - sub_bbox[0]
            sub_h = sub_bbox[3] - sub_bbox[1]
            draw.text((w - PADDING_X - sub_w, (h - sub_h) // 2), subtitle, font=self.font_stat_label, fill=TEXT_SECONDARY)
        draw.line([(0, h - 1), (w, h - 1)], fill=(40, 40, 55), width=1)

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

    # Shadowed text — drops a soft 2-pass shadow behind text. Cheap (two extra
    # draw.text calls), readable over any cover photo, looks consistent with
    # the rest of the dark UI.

    @staticmethod
    def _draw_text_shadow(
        draw: ImageDraw.Draw,
        xy: tuple,
        text: str,
        font,
        fill,
        *,
        shadow: bool = True,
        shadow_color=(0, 0, 0),
    ) -> None:
        """draw.text + drop shadow when `shadow=True`. Two passes (outer +2/+2
        and softer +1/+1) for a slight halo without an alpha layer."""
        if shadow:
            x, y = xy
            draw.text((x + 2, y + 2), text, font=font, fill=shadow_color)
            draw.text((x + 1, y + 1), text, font=font, fill=shadow_color)
        draw.text(xy, text, font=font, fill=fill)

    # Right-aligned text

    def _text_right(self, draw: ImageDraw.Draw, x_right: int, y: int, text: str, font, fill, *, shadow: bool = False):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        self._draw_text_shadow(draw, (x_right - tw, y), text, font, fill, shadow=shadow)

    # Center-aligned text

    def _text_center(self, draw: ImageDraw.Draw, cx: int, y: int, text: str, font, fill, *, shadow: bool = False):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        self._draw_text_shadow(draw, (cx - tw // 2, y), text, font, fill, shadow=shadow)

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

    # Mod badges — circular discs with white glyphs from osu-web SVGs.

    # Render badge at 4x the requested size, then downscale once with
    # LANCZOS — gives clean anti-aliased disc edges that PIL's ellipse()
    # can't produce directly at small sizes.
    _MOD_BADGE_SS: int = 4

    def _draw_mod_badge(
        self, img: Image.Image, x: int, y: int, mod: str, *, size: int = 24,
    ) -> int:
        """Draw a single colored disc with the mod's white glyph onto `img`.

        Supersampled (4×) then downscaled so the disc edge is smooth even at
        small sizes. Returns the x-coordinate just past the badge so callers
        can chain them. Falls back to the 2-letter code centred in the disc
        when the glyph PNG is missing from assets/icons/mods.
        """
        ss = self._MOD_BADGE_SS
        big = size * ss
        col = MOD_COLORS.get(mod, (100, 100, 120))

        disc = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        dd = ImageDraw.Draw(disc)
        dd.ellipse((0, 0, big - 1, big - 1), fill=col + (255,))
        # Faint rim — width scaled with supersample factor.
        dd.ellipse(
            (0, 0, big - 1, big - 1),
            outline=(0, 0, 0, 80), width=max(1, ss),
        )

        # Pull glyph at the supersampled inner size so it stays crisp.
        glyph_target = int(big * 0.78)
        glyph = load_mod_icon(mod, size=glyph_target) if mod else None
        if glyph is not None:
            disc.paste(
                glyph,
                ((big - glyph.width) // 2, (big - glyph.height) // 2),
                glyph,
            )

        disc = disc.resize((size, size), Image.LANCZOS)
        img.paste(disc, (x, y), disc)

        if glyph is None:
            d = ImageDraw.Draw(img)
            bb = d.textbbox((0, 0), mod, font=self.font_stat_label)
            tw = bb[2] - bb[0]
            th = bb[3] - bb[1]
            d.text(
                (x + (size - tw) // 2 - bb[0], y + (size - th) // 2 - bb[1]),
                mod, font=self.font_stat_label, fill=(255, 255, 255),
            )
        return x + size

    def _normalize_mods(self, mods) -> list[str]:
        """Coerce string / list / list-of-dicts mod inputs into [acronym, …]."""
        if not mods:
            return []
        if isinstance(mods, str):
            out = [m.strip() for m in mods.replace("+", "").split(",") if m.strip()]
        elif isinstance(mods, list):
            out = []
            for m in mods:
                if isinstance(m, str):
                    out.append(m.strip())
                elif isinstance(m, dict):
                    out.append(m.get("acronym", ""))
            out = [m for m in out if m]
        else:
            return []
        # CL (Classic) is auto-added by lazer — drop as visual noise.
        return [m for m in out if m != "CL"]

    def _draw_mod_badges(
        self, img: Image.Image, draw: ImageDraw.Draw, x: int, y: int, mods,
        *, size: int = 24, spacing: int = 4,
    ) -> ImageDraw.Draw:
        """Draw a left-aligned row of mod badges. `mods` accepts the same
        shapes as `_normalize_mods`. Returns the draw context unchanged.
        """
        cur_x = x
        for mod in self._normalize_mods(mods):
            cur_x = self._draw_mod_badge(img, cur_x, y, mod, size=size)
            cur_x += spacing
        return ImageDraw.Draw(img)

    # Save helper

    @staticmethod
    def _save(img: Image.Image) -> BytesIO:
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf
