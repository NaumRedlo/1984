import colorsys
import threading
from typing import Optional

TINTS = (
    (127, 184, 164),
    (110, 163, 230),
    (169, 135, 232),
    (227, 168, 87),
    (226, 72, 72),
)
RICHER = {1: 1.0, 2: 1.2, 3: 1.32, 4: 1.45}
SPREAD = {1: 0.0, 2: 18.0, 3: 30.0, 4: 44.0}

_held = threading.local()


def wear(palette: Optional[dict]) -> None:
    _held.palette = palette if palette and palette.get("rank") is not None else None


def worn() -> Optional[dict]:
    return getattr(_held, "palette", None)


def _stage(palette: dict) -> int:
    return max(1, min(4, int(palette.get("stage") or 1)))


def _warm(colour) -> bool:
    r, g, b = (v / 255 for v in colour[:3])
    hue, sat, _ = colorsys.rgb_to_hsv(r, g, b)
    return (hue < 0.06 or hue > 0.88) and sat > 0.12


def _turned(colour, turn: float = 0.0):
    palette = worn()
    if palette is None or not _warm(colour):
        return colour
    tier = TINTS[max(0, min(len(TINTS) - 1, int(palette["rank"])))]
    tier_hue, _, tier_sat = colorsys.rgb_to_hls(*(v / 255 for v in tier))
    hue, light, sat = colorsys.rgb_to_hls(*(v / 255 for v in colour[:3]))
    sat = min(1.0, max(sat * 0.6, tier_sat) * RICHER[_stage(palette)])
    r, g, b = colorsys.hls_to_rgb((tier_hue + turn / 360.0) % 1.0, light, sat)
    return tuple(round(v * 255) for v in (r, g, b)) + tuple(colour[3:])


def tone(colour):
    return _turned(colour)


def tone_for(palette: Optional[dict], colour):
    held = worn()
    wear(palette)
    try:
        return _turned(colour)
    finally:
        wear(held)


def title_colour(palette: Optional[dict]):
    if not palette or palette.get("rank") is None:
        return None
    held = worn()
    wear(palette)
    try:
        line = stops((226, 72, 72))
        return _along(line, 0.5) if len(line) > 2 else line[0][1]
    finally:
        wear(held)


def tones(start, end):
    palette = worn()
    if palette is None:
        return start, end
    spread = SPREAD[_stage(palette)]
    return _turned(start, -spread), _turned(end, spread)


def _lit(colour, k: float):
    return tuple(round(c + (255 - c) * k) for c in colour[:3])


def stops(colour) -> list:
    palette = worn()
    if palette is None or not _warm(colour):
        return [(0.0, tuple(colour[:3])), (1.0, tuple(colour[:3]))]
    base = _turned(colour)[:3]
    stage = _stage(palette)
    if stage == 1:
        return [(0.0, base), (1.0, base)]
    if stage == 2:
        return [(0.0, _turned(colour, -18)[:3]), (0.5, base), (1.0, _turned(colour, 22)[:3])]
    if stage == 3:
        return [(0.0, _turned(colour, -32)[:3]), (0.35, _lit(base, 0.22)), (0.55, base), (1.0, _turned(colour, 36)[:3])]
    return [(0.0, _turned(colour, -46)[:3]), (0.26, _lit(base, 0.55)), (0.48, base), (0.7, _lit(_turned(colour, 44), 0.4)), (1.0, _turned(colour, 52)[:3])]


def _along(line: list, at: float):
    for (left, a), (right, b) in zip(line, line[1:]):
        if at <= right:
            k = 0.0 if right == left else (at - left) / (right - left)
            return tuple(round(a[i] + (b[i] - a[i]) * k) for i in range(3))
    return line[-1][1]


def strip(w: int, h: int, colour, diagonal: bool = False):
    from PIL import Image

    line = stops(colour)
    picture = Image.new("RGB", (max(1, w), max(1, h)), line[0][1])
    if line[0][1] == line[-1][1] and len(line) == 2:
        return picture
    reach = (w * 0.866 + h * 0.5) if diagonal else max(1, w - 1)
    pixels = picture.load()
    if not diagonal:
        for x in range(w):
            c = _along(line, x / reach)
            for y in range(h):
                pixels[x, y] = c
        return picture
    for y in range(h):
        for x in range(w):
            pixels[x, y] = _along(line, (x * 0.866 + y * 0.5) / reach)
    return picture


def shimmers() -> bool:
    palette = worn()
    return palette is not None and _stage(palette) > 1


def glow(alpha: int) -> int:
    palette = worn()
    if palette is None:
        return alpha
    return min(255, round(alpha * (0.8, 1.0, 1.2, 1.45)[_stage(palette) - 1]))
