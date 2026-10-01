import math
from typing import Optional

from PIL import Image, ImageChops

FLOOR = 4.5
COVER_CEILING = 0.075
QUIET_CHROMA = 48
LIGHTEST = (245, 243, 246)
GROUND = (14, 12, 16)
STEPS = 16

def _linear(v: float) -> float:
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4

def _encoded(v: float) -> float:
    return v * 12.92 if v <= 0.0031308 else 1.055 * v ** (1 / 2.4) - 0.055

_TO_LINEAR = [_linear(v / 255) for v in range(256)]
_TO_LINEAR_8 = [round(255 * v) for v in _TO_LINEAR]

def luminance(colour) -> float:
    r, g, b = colour[:3]
    return 0.2126 * _TO_LINEAR[int(r)] + 0.7152 * _TO_LINEAR[int(g)] + 0.0722 * _TO_LINEAR[int(b)]

def contrast(a: float, b: float) -> float:
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)

def is_quiet(colour) -> bool:
    if not isinstance(colour, (tuple, list)) or len(colour) < 3:
        return False
    r, g, b = colour[:3]
    return max(r, g, b) - min(r, g, b) <= QUIET_CHROMA and luminance(colour) < luminance(LIGHTEST)

def backdrop(image: Image.Image, box) -> Optional[float]:
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(image.width, x1), min(image.height, y1)
    if x1 <= x0 or y1 <= y0:
        return None
    patch = image.crop((x0, y0, x1, y1))
    if patch.mode == "RGBA":
        alpha = patch.getchannel("A").resize((1, 1), Image.BOX).getpixel((0, 0))
        if alpha < 250:
            return None
    raw = patch.convert("RGB").resize((min(24, x1 - x0), min(6, y1 - y0)), Image.BOX).tobytes()
    seen = sorted(luminance(raw[at:at + 3]) for at in range(0, len(raw), 3))
    return seen[min(len(seen) - 1, int(len(seen) * 0.85))]

def readable(colour, under: Optional[float], floor: float = FLOOR):
    if under is None or not is_quiet(colour):
        return colour
    if contrast(luminance(colour), under) >= floor:
        return colour
    tail = tuple(colour[3:])
    for step in range(1, STEPS + 1):
        k = step / STEPS
        lifted = tuple(round(colour[i] + (LIGHTEST[i] - colour[i]) * k) for i in range(3))
        if contrast(luminance(lifted), under) >= floor:
            return lifted + tail
    return LIGHTEST + tail

def calm(art: Image.Image, ceiling: float = COVER_CEILING) -> Image.Image:
    rgb = art.convert("RGB")
    seen = rgb.point(_TO_LINEAR_8 * 3).convert("L", matrix=(0.2126, 0.7152, 0.0722, 0))
    cap = ceiling * 255
    knee = cap * 0.55
    gains = []
    for level in range(256):
        if level <= knee:
            gains.append(255)
            continue
        soft = knee + (cap - knee) * (1 - math.exp(-(level - knee) / (cap - knee)))
        gains.append(round(255 * _encoded(soft / 255) / _encoded(level / 255)))
    gain = seen.point(gains)
    calmed = ImageChops.multiply(rgb, Image.merge("RGB", (gain, gain, gain)))
    if art.mode == "RGBA":
        calmed = calmed.convert("RGBA")
        calmed.putalpha(art.getchannel("A"))
    return calmed
