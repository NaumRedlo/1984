import colorsys
import threading
from contextlib import contextmanager
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


@contextmanager
def wearing(palette: Optional[dict]):
    held = worn()
    wear(palette)
    try:
        yield worn()
    finally:
        wear(held)


def name(fill):
    return tone((255, 170, 162)) if worn() is not None else fill


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


class Toned(tuple):
    pass


class Plain(tuple):
    pass


def tone(colour):
    turned = _turned(colour)
    if turned is colour:
        return colour
    toned = Toned(turned)
    toned.source = colour
    return toned


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


PAIR = {2: 14.0, 3: 22.0, 4: 30.0}
PALE = {3: 0.6, 4: 0.5}


def stops(colour) -> list:
    palette = worn()
    if palette is None or not _warm(colour):
        return [(0.0, tuple(colour[:3])), (1.0, tuple(colour[:3]))]
    stage = _stage(palette)
    if stage == 1:
        base = _turned(colour)[:3]
        return [(0.0, base), (1.0, base)]
    spread = PAIR[stage]
    end = _turned(colour, spread)[:3]
    return [(0.0, _turned(colour, -spread)[:3]), (1.0, end)]


def two_tone(fill, size: float):
    palette = worn()
    if palette is None or _stage(palette) == 1 or isinstance(fill, Plain):
        return None
    if isinstance(fill, Toned):
        return stops(fill.source)
    stage = _stage(palette)
    if stage >= 3 and size >= 20 and min(fill[:3]) >= 215 and max(fill[:3]) - min(fill[:3]) <= 12:
        return [(at, _lit(c, PALE[stage])) for at, c in stops((226, 72, 72))]
    return None


def paint(w: int, h: int, line: list):
    from PIL import Image

    picture = Image.new("RGB", (max(1, w), max(1, h)), line[0][1])
    reach = w * 0.866 + h * 0.5
    pixels = picture.load()
    for y in range(h):
        for x in range(w):
            pixels[x, y] = _along(line, (x * 0.866 + y * 0.5) / reach)
    return picture


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
