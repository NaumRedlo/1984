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


def tones(start, end):
    palette = worn()
    if palette is None:
        return start, end
    spread = SPREAD[_stage(palette)]
    return _turned(start, -spread), _turned(end, spread)
