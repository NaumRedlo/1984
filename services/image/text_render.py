from __future__ import annotations

import os
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageColor, ImageDraw, ImageFont
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

# Fonts drawn the careful way below: (letter-spacing, word space) in em, None keeping the font's
# own space. Nunito needs neither, only the even gaps.
_LOOSE_FAMILIES = {"Nunito": (0.0, None)}

def _looseness(font) -> Optional[tuple[float, float]]:
    path = _path_of(font) or ""
    size = getattr(font, "size", 0) or 0
    for family, (track, space) in _LOOSE_FAMILIES.items():
        if family in os.path.basename(path):
            return track * size, (space * size if space is not None else None)
    return None

_SCALE = 4

@lru_cache(maxsize=64)
def _unhinted(path: str, size: int):
    return ImageFont.truetype(path, size * _SCALE)

def _advances(run: str, font) -> list[float]:
    """Where each character of the run starts and where the run ends, kerning kept.

    Widths come from the font at four times the size, so hinting does not round each
    letter to whole pixels and leave uneven gaps ("Snow Ni No" for "SnowNiNo")."""
    loose = _looseness(font)
    if loose is None:
        return [0.0, float(font.getlength(run))]
    track, space = loose
    big = _unhinted(_path_of(font), font.size)
    marks, x, prev = [], 0.0, 0.0
    for i, ch in enumerate(run):
        marks.append(x)
        here = big.getlength(run[:i + 1]) / _SCALE
        step = space if ch == " " and space is not None else here - prev
        prev = here
        x += step + track
    marks.append(x - track)
    return marks

def _run_width(draw: ImageDraw.ImageDraw, run: str, font) -> int:
    try:
        return int(round(_advances(run, font)[-1]))
    except Exception:
        return sum(_glyph_width(draw, ch, font) for ch in run)

def _draw_run(draw: ImageDraw.ImageDraw, x: float, y: float, run: str, font, fill) -> None:
    """Loose fonts are drawn at four times the size and scaled down, as a browser would:
    at small sizes hinting moves each letter a pixel or so and the gaps come out uneven."""
    image = getattr(draw, "_image", None)
    try:
        marks = _advances(run, font)
    except Exception:
        marks = [0.0, 0.0]
    if len(marks) == 2 and not run.strip():
        return
    if _looseness(font) is None or image is None:
        draw.text((x, y), run, font=font, fill=fill)
        return
    big = _unhinted(_path_of(font), font.size)
    pad = font.size
    ix, iy = int(x) - pad, int(y) - pad
    ox, oy = (x - ix) * _SCALE, (y - iy) * _SCALE
    w = int(marks[-1] + 2 * pad + 2) * _SCALE
    h = int(font.size * 2 + 2 * pad) * _SCALE
    mask = Image.new("L", (w, h), 0)
    md = ImageDraw.Draw(mask)
    for ch, dx in zip(run, marks):
        if ch != " ":
            md.text((ox + dx * _SCALE, oy), ch, font=big, fill=255)
    mask = mask.reduce(_SCALE)
    if isinstance(fill, str):
        fill = ImageColor.getcolor(fill, image.mode)
    elif image.mode == "RGBA" and len(fill) == 3:
        fill = tuple(fill) + (255,)
    image.paste(fill, (ix, iy), mask)

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
            _draw_run(draw, x + 1, y + 1, run, f, shadow_color)
        _draw_run(draw, x, y, run, f, fill)
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
