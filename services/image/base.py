"""
BaseCardRenderer — shared drawing primitives for all card types.
"""

from io import BytesIO
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from utils.logger import get_logger
from services.image.constants import (
    BG_COLOR, TEXT_PRIMARY, TEXT_SECONDARY,
    ACCENT_RED, PANEL_BG, MOD_COLORS, MOD_ACRONYMS,
    PADDING_X,
    TORUS_BOLD, TORUS_SEMI, TORUS_REG,
    MPLUS_BOLD, MPLUS_REG,
    PROXIMA_BOLD, PROXIMA_SEMI, PROXIMA_REG,
)
from services.image.utils import _find_font, load_mod_icon, cover_center_crop
from services.image.text_render import (
    draw_text_multifont, text_size_multifont,
)

logger = get_logger("services.image_gen")


class BaseCardRenderer:
    """Shared drawing primitives for all card types."""

    def __init__(self):
        bold = _find_font(TORUS_BOLD)
        semi = _find_font(TORUS_SEMI)
        reg = _find_font(TORUS_REG)

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


        # CJK / Cyrillic / Greek / Hiragana / Katakana fallback. Sized to
        # match each primary slot so mixed-script strings line up on the
        # baseline without a visible step. `_draw_text` / `_text_right` /
        # `_text_center` pick this transparently for unsupported glyphs.
        mp_bold = _find_font(MPLUS_BOLD)
        mp_reg  = _find_font(MPLUS_REG)
        if mp_bold:
            self.fb_title       = ImageFont.truetype(mp_bold, 28)
            self.fb_big         = ImageFont.truetype(mp_bold, 34)
            self.fb_subtitle    = ImageFont.truetype(mp_reg or mp_bold, 20)
            self.fb_row         = ImageFont.truetype(mp_bold, 22)
            self.fb_grade       = ImageFont.truetype(mp_bold, 40)
            self.fb_label       = ImageFont.truetype(mp_reg or mp_bold, 18)
            self.fb_small       = ImageFont.truetype(mp_reg or mp_bold, 16)
            self.fb_vs          = ImageFont.truetype(mp_bold, 48)
            self.fb_stat_value  = ImageFont.truetype(mp_bold, 26)
            self.fb_stat_label  = ImageFont.truetype(mp_reg or mp_bold, 14)
        else:
            # No CJK font available — `_font_fallback` returns None and
            # `draw_text_multifont` degrades to single-font behaviour
            # (tofu boxes for unsupported codepoints).
            self.fb_title = self.fb_big = self.fb_subtitle = None
            self.fb_row = self.fb_grade = self.fb_label = None
            self.fb_small = self.fb_vs = None
            self.fb_stat_value = self.fb_stat_label = None

        # Map every primary font slot to its CJK fallback. `_font_fallback`
        # uses this when drawing user-supplied text. Keyed by `id(font)`
        # so the lookup is an identity-cheap dict hit.
        self._fb_map: dict[int, object] = {
            id(self.font_title):       self.fb_title,
            id(self.font_big):         self.fb_big,
            id(self.font_subtitle):    self.fb_subtitle,
            id(self.font_row):         self.fb_row,
            id(self.font_grade):       self.fb_grade,
            id(self.font_label):       self.fb_label,
            id(self.font_small):       self.fb_small,
            id(self.font_vs):          self.fb_vs,
            id(self.font_stat_value):  self.fb_stat_value,
            id(self.font_stat_label):  self.fb_stat_label,
        }

        # Cyrillic-specific fallback (2026-07-02): ProximaSoft, weight-matched
        # to each primary slot the same way the primary fonts themselves are
        # built above (bold slots -> Proxima Bold, semi/reg slots -> their
        # Proxima equivalents). Takes priority over the general CJK fallback
        # for Cyrillic-block characters — see text_render._pick_font.
        px_bold = _find_font(PROXIMA_BOLD)
        px_semi = _find_font(PROXIMA_SEMI)
        px_reg  = _find_font(PROXIMA_REG)
        if px_bold:
            self.fbcy_title       = ImageFont.truetype(px_bold, 28)
            self.fbcy_big         = ImageFont.truetype(px_bold, 34)
            self.fbcy_subtitle    = ImageFont.truetype(px_semi or px_bold, 20)
            self.fbcy_row         = ImageFont.truetype(px_bold, 22)
            self.fbcy_grade       = ImageFont.truetype(px_bold, 40)
            self.fbcy_label       = ImageFont.truetype(px_semi or px_bold, 18)
            self.fbcy_small       = ImageFont.truetype(px_reg or px_bold, 16)
            self.fbcy_vs          = ImageFont.truetype(px_bold, 48)
            self.fbcy_stat_value  = ImageFont.truetype(px_bold, 26)
            self.fbcy_stat_label  = ImageFont.truetype(px_semi or px_bold, 14)
        else:
            self.fbcy_title = self.fbcy_big = self.fbcy_subtitle = None
            self.fbcy_row = self.fbcy_grade = self.fbcy_label = None
            self.fbcy_small = self.fbcy_vs = None
            self.fbcy_stat_value = self.fbcy_stat_label = None

        self._fb_cyrillic_map: dict[int, object] = {
            id(self.font_title):       self.fbcy_title,
            id(self.font_big):         self.fbcy_big,
            id(self.font_subtitle):    self.fbcy_subtitle,
            id(self.font_row):         self.fbcy_row,
            id(self.font_grade):       self.fbcy_grade,
            id(self.font_label):       self.fbcy_label,
            id(self.font_small):       self.fbcy_small,
            id(self.font_vs):          self.fbcy_vs,
            id(self.font_stat_value):  self.fbcy_stat_value,
            id(self.font_stat_label):  self.fbcy_stat_label,
        }

    def _font_fallback(self, font):
        """Return the CJK fallback sized to match `font`, or None if missing."""
        return self._fb_map.get(id(font))

    def _font_cyrillic_fallback(self, font):
        """Return the Cyrillic-specific (Proxima) fallback for `font`, or
        None if missing — checked before the general CJK fallback."""
        return self._fb_cyrillic_map.get(id(font))

    # Canvas

    def _create_canvas(self, w: int, h: int):
        img = Image.new("RGB", (w, h), BG_COLOR)
        draw = ImageDraw.Draw(img)
        return img, draw

    # Multi-font text — primary glyph where covered, CJK fallback elsewhere.
    # All shared text-drawing helpers below route through this; subclass
    # renders that call `draw.text` directly bypass the fallback and will
    # tofu on user-supplied non-Latin content.

    def _draw_text(
        self,
        draw: ImageDraw.Draw,
        xy: tuple,
        text: str,
        font,
        fill,
        *,
        shadow: bool = False,
        shadow_color=(0, 0, 0),
    ) -> int:
        """`draw.text` with per-character Cyrillic/CJK fallback. Returns end-x."""
        fb = self._font_fallback(font)
        cyfb = self._font_cyrillic_fallback(font)
        return draw_text_multifont(
            draw, xy, text, font, fb, fill,
            cyrillic_fallback=cyfb, shadow=shadow, shadow_color=shadow_color,
        )

    def _text_size(self, draw: ImageDraw.Draw, text: str, font) -> tuple[int, int]:
        """(width, height) with Cyrillic/CJK fallback per glyph."""
        fb = self._font_fallback(font)
        cyfb = self._font_cyrillic_fallback(font)
        return text_size_multifont(draw, text, font, fb, cyrillic_fallback=cyfb)

    # Header

    def _draw_header(self, draw: ImageDraw.Draw, title: str, subtitle: str, w: int):
        """Compact 28px header: title left-aligned in accent red, subtitle right-aligned gray."""
        h = 28
        draw.rectangle([(0, 0), (w, h)], fill=(18, 18, 28))
        title_w, title_h = self._text_size(draw, title, self.font_stat_label)
        self._draw_text(draw, (PADDING_X, (h - title_h) // 2), title, self.font_stat_label, ACCENT_RED)
        if subtitle:
            sub_w, sub_h = self._text_size(draw, subtitle, self.font_stat_label)
            self._draw_text(draw, (w - PADDING_X - sub_w, (h - sub_h) // 2), subtitle, self.font_stat_label, TEXT_SECONDARY)
        draw.line([(0, h - 1), (w, h - 1)], fill=(40, 40, 55), width=1)

    # Footer

    def _draw_footer(self, draw: ImageDraw.Draw, img: Image.Image, text: str, y: int, w: int):
        draw.line([(0, y), (w, y)], fill=ACCENT_RED, width=1)
        self._draw_text(draw, (PADDING_X, y + 6), text, self.font_small, TEXT_SECONDARY)

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
        lf = label_font or self.font_label
        vf = value_font or self.font_row
        lc = label_fill or TEXT_SECONDARY
        vc = value_fill or TEXT_PRIMARY
        label_str = f"{label}:"
        self._draw_text(draw, (x, y), label_str, lf, lc)
        lw, _ = self._text_size(draw, label_str, lf)
        self._draw_text(draw, (x + lw + 8, y), value, vf, vc)

    # Section title

    def _draw_section_title(self, draw: ImageDraw.Draw, y: int, text: str):
        self._draw_text(draw, (PADDING_X, y), text, self.font_subtitle, ACCENT_RED)

    # Shadowed text — drops a soft 2-pass shadow behind text. Cheap (two extra
    # draw.text calls), readable over any cover photo, looks consistent with
    # the rest of the dark UI.

    def _draw_text_shadow(
        self,
        draw: ImageDraw.Draw,
        xy: tuple,
        text: str,
        font,
        fill,
        *,
        shadow: bool = True,
        shadow_color=(0, 0, 0),
    ) -> None:
        """`_draw_text` + drop shadow when `shadow=True`. Two passes (outer
        +2/+2 and softer +1/+1) for a slight halo without an alpha layer.
        Routes through multifont so cyrillic / CJK fall back to MPLUS."""
        if shadow:
            x, y = xy
            self._draw_text(draw, (x + 2, y + 2), text, font, shadow_color)
            self._draw_text(draw, (x + 1, y + 1), text, font, shadow_color)
        self._draw_text(draw, xy, text, font, fill)

    # Right-aligned text

    def _text_right(self, draw: ImageDraw.Draw, x_right: int, y: int, text: str, font, fill, *, shadow: bool = False):
        tw, _ = self._text_size(draw, text, font)
        self._draw_text_shadow(draw, (x_right - tw, y), text, font, fill, shadow=shadow)

    # Center-aligned text

    def _text_center(self, draw: ImageDraw.Draw, cx: int, y: int, text: str, font, fill, *, shadow: bool = False):
        tw, _ = self._text_size(draw, text, font)
        self._draw_text_shadow(draw, (cx - tw // 2, y), text, font, fill, shadow=shadow)

    # Panel (rounded rect bg)

    def _draw_panel(self, draw: ImageDraw.Draw, x: int, y: int, w: int, h: int, bg=PANEL_BG):
        draw.rounded_rectangle((x, y, x + w, y + h), radius=8, fill=bg)

    # Stat cell (value on top, label below)

    def _draw_stat_cell(self, draw: ImageDraw.Draw, cx: int, y: int, value: str, label: str):
        self._text_center(draw, cx, y, value, self.font_stat_value, TEXT_PRIMARY)
        self._text_center(draw, cx, y + 30, label, self.font_stat_label, TEXT_SECONDARY)

    def _draw_mini_badge(self, draw: ImageDraw.Draw, x: int, y: int, w: int, h: int, value: str, label: str):
        self._draw_panel(draw, x, y, w, h)
        self._text_center(draw, x + w // 2, y + 2, value, self.font_small, TEXT_PRIMARY)
        self._text_center(draw, x + w // 2, y + h - 12, label, self.font_stat_label, TEXT_SECONDARY)

    # AA outline helpers — supersample at 4× and downscale with LANCZOS so
    # the curve where a rounded corner meets the straight edge doesn't
    # show the visible step PIL's `rounded_rectangle(outline=...)` leaves
    # at small radii (radius<20, width≥2). Same trick as `_draw_mod_badge`.
    # Used by avatar outlines, podium frames, badge rims.
    _AA_OUTLINE_SS: int = 4

    def _aa_rounded_outline(
        self,
        img: Image.Image,
        box: tuple[int, int, int, int],
        *,
        radius: int,
        outline,
        width: int = 2,
        fill=None,
    ) -> None:
        """Drop-in AA replacement for `draw.rounded_rectangle(outline=...)`.

        Renders at 4× into an RGBA layer, downscales LANCZOS, pastes onto
        `img`. `fill` is rendered at full size on the supersampled layer
        so a filled-and-outlined panel comes out with one clean blend.
        """
        ss = self._AA_OUTLINE_SS
        x0, y0, x1, y1 = box
        w = (x1 - x0) * ss
        h = (y1 - y0) * ss
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        kwargs = {"radius": radius * ss, "outline": outline, "width": width * ss}
        if fill is not None:
            kwargs["fill"] = fill
        d.rounded_rectangle((0, 0, w - 1, h - 1), **kwargs)
        layer = layer.resize((x1 - x0, y1 - y0), Image.LANCZOS)
        img.paste(layer, (x0, y0), layer)

    def _rounded_mask(self, size: tuple[int, int], radius: int) -> Image.Image:
        """Anti-aliased 'L' alpha mask: a filled rounded rectangle.

        Supersampled 4× then LANCZOS-downscaled so the corners come out
        smooth.  Pass as the mask arg to ``img.paste(panel, xy, mask)`` to
        give a pasted (square) panel rounded corners that line up with an
        ``_aa_rounded_outline`` frame of the same radius.
        """
        ss = self._AA_OUTLINE_SS
        w, h = size
        big = Image.new("L", (w * ss, h * ss), 0)
        d = ImageDraw.Draw(big)
        d.rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=radius * ss, fill=255)
        return big.resize((w, h), Image.LANCZOS)

    def _aa_ellipse_outline(
        self,
        img: Image.Image,
        box: tuple[int, int, int, int],
        *,
        outline,
        width: int = 2,
        fill=None,
    ) -> None:
        """Drop-in AA replacement for `draw.ellipse(outline=...)`."""
        ss = self._AA_OUTLINE_SS
        x0, y0, x1, y1 = box
        w = (x1 - x0) * ss
        h = (y1 - y0) * ss
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        kwargs = {"outline": outline, "width": width * ss}
        if fill is not None:
            kwargs["fill"] = fill
        d.ellipse((0, 0, w - 1, h - 1), **kwargs)
        layer = layer.resize((x1 - x0, y1 - y0), Image.LANCZOS)
        img.paste(layer, (x0, y0), layer)

    def _aa_rounded_fill(self, img: Image.Image, box: tuple[int, int, int, int], *, radius: int, fill) -> None:
        """AA drop-in for a filled `draw.rounded_rectangle` (pills, badges).

        Supersampled so the rounded corners come out smooth at the small radii
        badges use — where PIL's direct fill leaves a visible step.
        """
        self._aa_rounded_outline(img, box, radius=radius, outline=None, fill=fill)

    def _aa_ellipse_fill(self, img: Image.Image, box: tuple[int, int, int, int], *, fill) -> None:
        """AA drop-in for a filled `draw.ellipse` (disc badges, slot circles)."""
        self._aa_ellipse_outline(img, box, outline=None, fill=fill)

    # ── Unified graph standard ────────────────────────────────────────────
    # Every card's line graph (strain, pp/rank history, etc.) goes through
    # these two: _smooth_points turns N raw samples into a dense Catmull-Rom
    # curve (no "staircase" between sparse points), _aa_graph_curve draws
    # that curve supersampled+downscaled (no pixel staircase from PIL's
    # non-anti-aliased line drawing). Neither one changes the underlying
    # data — smoothing interpolates the exact same values, AA only affects
    # how the pixels blend.

    @staticmethod
    def _smooth_points(points: list, samples_per_segment: int = 8) -> list:
        """Catmull-Rom spline through arbitrary (x, y) points, densified for
        a smooth curve. Standard Catmull-Rom boundary handling — the nearest
        endpoint stands in for the missing neighbour at each end. `points`
        must be sorted by x (evenly spaced or not; the spline runs over
        point INDEX, not x-distance, matching how every current caller
        already spaces its samples evenly). 2 points → a straight line
        (nothing to interpolate); 1 point or fewer is returned as-is."""
        n = len(points)
        if n < 3:
            return list(points)
        total = max(2, (n - 1) * samples_per_segment + 1)
        out = []
        for j in range(total):
            pos = j / (total - 1) * (n - 1)
            i = min(int(pos), n - 2)
            t = pos - i
            p0 = points[i - 1] if i - 1 >= 0 else points[i]
            p1 = points[i]
            p2 = points[i + 1]
            p3 = points[i + 2] if i + 2 < n else points[i + 1]
            t2, t3 = t * t, t * t * t
            x = 0.5 * (2 * p1[0] + (-p0[0] + p2[0]) * t
                       + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                       + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            y = 0.5 * (2 * p1[1] + (-p0[1] + p2[1]) * t
                       + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                       + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, y))
        return out

    def _aa_graph_curve(self, img: Image.Image, x: int, y: int, w: int, h: int,
                        points: list, *, line_color, line_width: int = 3,
                        fill_color=None) -> None:
        """Anti-aliased polyline (+ optional gradient fill down to the plot
        box's bottom edge) through `points` (already in `img`'s pixel space,
        expected to sit within the (x, y, w, h) plot box — pass through
        `_smooth_points` first for a curve rather than a raw sparse
        polyline). Supersampled at `_AA_OUTLINE_SS` then LANCZOS-downscaled,
        same technique as `_aa_rounded_fill`/`_aa_rounded_outline` — the
        single implementation every card's graph line renders through, so
        "what a graph looks like" is defined in one place."""
        if len(points) < 2:
            return
        ss = self._AA_OUTLINE_SS
        pad = line_width + 2  # stroke margin so the join isn't clipped at the layer edge
        lw, lh = int(w + pad * 2), int(h + pad * 2)
        if lw <= 0 or lh <= 0:
            return
        layer = Image.new("RGBA", (lw * ss, lh * ss), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ss_pts = [((px - x + pad) * ss, (py - y + pad) * ss) for px, py in points]
        if fill_color is not None:
            bottom = (h + pad) * ss
            poly = ss_pts + [(ss_pts[-1][0], bottom), (ss_pts[0][0], bottom)]
            ld.polygon(poly, fill=fill_color)
        ld.line(ss_pts, fill=line_color, width=max(1, line_width * ss), joint="curve")
        layer = layer.resize((lw, lh), Image.LANCZOS)
        img.paste(layer, (int(x - pad), int(y - pad)), layer)

    # ── Unified cover-bleed standard ─────────────────────────────────────
    # A duplicated map/profile cover reading across a whole panel, muted on
    # the left ramping up to vivid on the right — used by recent.py's hero
    # (rs and, via the same renderer, the score-link card), map_card.py's
    # header, and profile.py's hero. Previously each drew its own version
    # that only faded IN starting partway across (zero-alpha, i.e.
    # invisible, on the left) instead of covering the full width at a
    # muted alpha.

    def _cover_bleed(self, cover: Image.Image, w: int, h: int, *,
                     min_alpha: int = 50, max_alpha: int = 235,
                     darken_alpha: int = 120, radius: int = 14,
                     corner_mask: Optional[Image.Image] = None) -> Image.Image:
        """Cover art bled across a (w, h) panel: a flat `darken_alpha` black
        overlay (keeps text readable even at max_alpha) under a left-to-right
        linear alpha ramp from `min_alpha` (muted — mostly the panel's own
        flat colour shows through) to `max_alpha` (vivid). Corner-masked to
        a simple `radius`-rounded rect by default; pass `corner_mask` (an
        'L' image the same size as `(w, h)`) instead for anything irregular
        — e.g. profile.py's hero, which only rounds its TOP corners since
        it's the top slice of a taller card. Returns an RGBA image ready to
        paste directly:
        `bled = self._cover_bleed(cover, w, h); img.paste(bled.convert("RGB"), (x, y), bled)`."""
        from PIL import ImageChops
        bg = cover_center_crop(cover.convert("RGBA"), w, h)
        bg = Image.alpha_composite(bg, Image.new("RGBA", (w, h), (0, 0, 0, darken_alpha)))
        ramp = Image.new("L", (w, h), 0)
        rd = ImageDraw.Draw(ramp)
        for fx in range(w):
            a = min_alpha + (max_alpha - min_alpha) * (fx / max(1, w - 1))
            rd.line([(fx, 0), (fx, h)], fill=int(a))
        mask = corner_mask if corner_mask is not None else self._rounded_mask((w, h), radius)
        ramp = ImageChops.multiply(ramp, mask)
        bg.putalpha(ramp)
        return bg

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
            raw = [m.strip() for m in mods.replace("+", ",").split(",") if m.strip()]
        elif isinstance(mods, list):
            raw = []
            for m in mods:
                if isinstance(m, str):
                    raw.append(m.strip())
                elif isinstance(m, dict):
                    raw.append(m.get("acronym", ""))
            raw = [m for m in raw if m]
        else:
            return []
        # A token may be several acronyms glued together ("HDDT"); split it by
        # longest-known-acronym-first so each mod becomes its own badge.
        out = []
        for tok in raw:
            out.extend(self._split_mod_token(tok.upper()))
        # CL (Classic) is auto-added by lazer — drop as visual noise.
        return [m for m in out if m != "CL"]

    @staticmethod
    def _split_mod_token(tok: str) -> list[str]:
        """Greedily split a concatenated acronym string into known mods.
        Unknown remainders are kept as-is so nothing silently vanishes."""
        out, i, n = [], 0, len(tok)
        while i < n:
            # Try 3-char (SV2) then 2-char acronyms before giving up.
            for size in (3, 2):
                if tok[i:i + size] in MOD_ACRONYMS:
                    out.append(tok[i:i + size])
                    i += size
                    break
            else:
                out.append(tok[i:i + 2])
                i += 2
        return out

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
