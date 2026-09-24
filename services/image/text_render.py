from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from PIL import ImageDraw, ImageFont
from fontTools.ttLib import TTFont

_CYRILLIC_RANGE = range(0x0400, 0x0500)

@lru_cache(maxsize=16)
def _font_coverage(font_path: str) -> frozenset[int]:
    try:
        tt = TTFont(font_path, lazy=True)
        cmap = tt.getBestCmap() or {}
        return frozenset(cmap.keys())
    except Exception:
        return frozenset()

def _path_of(font: ImageFont.FreeTypeFont) -> Optional[str]:
    p = getattr(font, "path", None)
    if isinstance(p, str) and os.path.isfile(p):
        return p
    return None

def _covers(font: ImageFont.FreeTypeFont, ch: str) -> bool:
    if ch.isspace() or ord(ch) < 0x20:
        return True
    path = _path_of(font)
    if path is None:
        return True
    coverage = _font_coverage(path)
    if not coverage:
        return True
    return ord(ch) in coverage

def _glyph_width(draw: ImageDraw.ImageDraw, ch: str, font) -> int:
    bbox = draw.textbbox((0, 0), ch, font=font)

    w = bbox[2] - bbox[0]
    if w <= 0:
        try:
            return int(font.getlength(ch))
        except Exception:
            return 0
    return w

def _pick_font(ch: str, primary, fallback, cyrillic_fallback):
    if _covers(primary, ch):
        return primary
    if cyrillic_fallback is not None and ord(ch) in _CYRILLIC_RANGE:
        return cyrillic_fallback
    return fallback if fallback is not None else primary

def _runs(text: str, primary, fallback, cyrillic_fallback):
    runs: list[tuple[object, str]] = []
    for ch in text:
        f = _pick_font(ch, primary, fallback, cyrillic_fallback)
        if runs and runs[-1][0] is f:
            runs[-1] = (f, runs[-1][1] + ch)
        else:
            runs.append((f, ch))
    return runs

def _run_width(draw: ImageDraw.ImageDraw, run: str, font) -> int:
    try:
        return int(round(font.getlength(run)))
    except Exception:
        return sum(_glyph_width(draw, ch, font) for ch in run)

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
    if not text:
        return xy[0]
    x, y = xy
    for f, run in _runs(text, primary, fallback, cyrillic_fallback):
        if shadow:
            draw.text((x + 1, y + 1), run, font=f, fill=shadow_color)
        draw.text((x, y), run, font=f, fill=fill)
        x += _run_width(draw, run, f)
    return x

def text_size_multifont(
    draw: ImageDraw.ImageDraw,
    text: str,
    primary,
    fallback,
    *,
    cyrillic_fallback=None,
) -> tuple[int, int]:
    if not text:
        return 0, 0
    width = 0
    height = 0
    for f, run in _runs(text, primary, fallback, cyrillic_fallback):
        width += _run_width(draw, run, f)
        bbox = draw.textbbox((0, 0), run, font=f)
        height = max(height, bbox[3] - bbox[1])
    return width, height

__all__ = [
    "draw_text_multifont",
    "text_size_multifont",
    "_font_coverage",
    "_covers",
    "_CYRILLIC_RANGE",
]
