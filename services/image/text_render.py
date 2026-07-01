"""Multi-font text rendering — falls back to a secondary font for glyphs
the primary doesn't cover.

The osu! brand font we ship (TorusNotched) covers Basic Latin + Latin-1
Supplement + Latin Extended-A only — 362 glyphs total. User-supplied
text routinely contains characters outside this range: osu! beatmap
titles and artists in Japanese, player nicknames in Cyrillic, song
names with Greek letters. Pillow has no built-in fallback chain — it
renders unsupported codepoints as the font's `.notdef` glyph (the tofu
box) and moves on.

This module ships two helpers:

  * `draw_text_multifont(draw, xy, text, primary, fallback, fill, ...)`
    Draws each character with `primary` if its codepoint is covered,
    `fallback` otherwise. Advances by the actual glyph width each
    character, so the rendered string has correct kerning.

  * `text_size_multifont(text, primary, fallback)` — sum of per-glyph
    widths under the same fallback rule; used for centering / right-
    aligning text that mixes scripts.

The fallback font we currently use is M PLUS Rounded 1c (8201 glyphs,
covers Latin + Cyrillic + Greek + Hiragana + Katakana + CJK + symbols).
Korean Hangul is the one common script it lacks; those still tofu.

A second, more specific fallback (`cyrillic_fallback`, added 2026-07-02) takes
priority over the general one for characters in the Cyrillic block: ProximaSoft
covers full Cyrillic + ASCII and matches the brand's visual style better than
MPLUS for that script specifically. MPLUS remains the fallback for everything
else (CJK, Greek, symbols) since Proxima doesn't cover those.

`_font_coverage` is cached per font path (`@lru_cache`), so the
per-character coverage check is a single frozenset membership lookup
once the cache is warm — cheap enough to call on every text draw.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from PIL import ImageDraw, ImageFont
from fontTools.ttLib import TTFont

# Unicode block "Cyrillic" (0400-04FF) — covers the full Russian alphabet plus
# the rest of the block's extended letters.
_CYRILLIC_RANGE = range(0x0400, 0x0500)


# ── Coverage cache ──────────────────────────────────────────────────────


@lru_cache(maxsize=16)
def _font_coverage(font_path: str) -> frozenset[int]:
    """Return the set of codepoints this TTF/OTF can render. Cached per path.

    Returns an empty frozenset if the font can't be inspected; the
    drawing helpers treat that as "assume covers everything" so a
    misconfigured deploy doesn't garble all text.
    """
    try:
        tt = TTFont(font_path, lazy=True)
        cmap = tt.getBestCmap() or {}
        return frozenset(cmap.keys())
    except Exception:
        return frozenset()


def _path_of(font: ImageFont.FreeTypeFont) -> Optional[str]:
    """Pull the TTF/OTF path out of a Pillow font, if it has one."""
    p = getattr(font, "path", None)
    if isinstance(p, str) and os.path.isfile(p):
        return p
    return None


def _covers(font: ImageFont.FreeTypeFont, ch: str) -> bool:
    """True if `font` has a glyph for `ch`.

    `space` and control characters always return True — they don't
    render to tofu even when nominally outside coverage.
    """
    if ch.isspace() or ord(ch) < 0x20:
        return True
    path = _path_of(font)
    if path is None:
        return True  # unknown font — let it through
    coverage = _font_coverage(path)
    if not coverage:
        return True
    return ord(ch) in coverage


# ── Public draw helpers ────────────────────────────────────────────────


def _glyph_width(draw: ImageDraw.ImageDraw, ch: str, font) -> int:
    """Pixel advance width of a single character with `font`."""
    bbox = draw.textbbox((0, 0), ch, font=font)
    # Use the right edge of the bbox (textbbox returns left+right of the
    # rendered glyph). For space characters bbox can be empty; fall back
    # to getlength when that happens.
    w = bbox[2] - bbox[0]
    if w <= 0:
        try:
            return int(font.getlength(ch))
        except Exception:
            return 0
    return w


def _pick_font(ch: str, primary, fallback, cyrillic_fallback):
    """Primary if it covers `ch`; else the Cyrillic-specific fallback for
    Cyrillic-block characters (if provided), else the general fallback."""
    if _covers(primary, ch):
        return primary
    if cyrillic_fallback is not None and ord(ch) in _CYRILLIC_RANGE:
        return cyrillic_fallback
    return fallback if fallback is not None else primary


def draw_text_multifont(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    primary,
    fallback,
    fill,
    *,
    cyrillic_fallback=None,
    shadow: bool = False,
    shadow_color=(0, 0, 0),
) -> int:
    """Draw `text` character-by-character: primary where covered, then
    `cyrillic_fallback` for Cyrillic-block glyphs it doesn't cover, then the
    general `fallback` for anything else. Returns the x-coordinate just past
    the rendered string.

    `fallback=None` / `cyrillic_fallback=None` degrade gracefully.

    `shadow=True` draws each character twice — once at +1/+1 in
    `shadow_color`, once at the real position in `fill` — same cheap
    drop-shadow style used by `BaseCardRenderer._draw_text_shadow`.
    """
    if not text:
        return xy[0]
    x, y = xy
    for ch in text:
        f = _pick_font(ch, primary, fallback, cyrillic_fallback)
        if shadow:
            draw.text((x + 1, y + 1), ch, font=f, fill=shadow_color)
        draw.text((x, y), ch, font=f, fill=fill)
        x += _glyph_width(draw, ch, f)
    return x


def text_size_multifont(
    draw: ImageDraw.ImageDraw,
    text: str,
    primary,
    fallback,
    *,
    cyrillic_fallback=None,
) -> tuple[int, int]:
    """Return (width, height) the multifont string would render to.

    Height is the max ascent/descent of whichever font is used per char.
    Width is the sum of per-glyph advances.
    """
    if not text:
        return 0, 0
    width = 0
    height = 0
    for ch in text:
        f = _pick_font(ch, primary, fallback, cyrillic_fallback)
        width += _glyph_width(draw, ch, f)
        bbox = draw.textbbox((0, 0), ch, font=f)
        h = bbox[3] - bbox[1]
        if h > height:
            height = h
    return width, height


__all__ = [
    "draw_text_multifont",
    "text_size_multifont",
    "_font_coverage",
    "_covers",
    "_CYRILLIC_RANGE",
]
