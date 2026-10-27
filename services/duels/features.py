import math
from statistics import median
from typing import Any

from services.duels.osu_file import Beatmap, HitObject

FEATURE_VERSION = "features-v2"
WINDOWS = (2.0, 10.0, 30.0)
STREAM_SPREAD = 0.12
STREAM_LEAST = 3
STREAM_BINS = ((3, 5), (6, 15), (16, 31), (32, None))
LOADED = 0.5

def radius(cs: float) -> float:
    return 54.4 - 4.48 * cs

def preempt(ar: float) -> float:
    return 1200 + 600 * (5 - ar) / 5 if ar < 5 else 1200 - 750 * (ar - 5) / 5

def apply_mods(beatmap: Beatmap, mods: str) -> tuple[float, float, float, float, bool]:
    cs, ar, od, rate = beatmap.cs, beatmap.ar, beatmap.od, 1.0
    if "HR" in mods:
        cs, ar, od = min(10.0, cs * 1.3), min(10.0, ar * 1.4), min(10.0, od * 1.4)
    if "EZ" in mods:
        cs, ar, od = cs / 2, ar / 2, od / 2
    if "DT" in mods or "NC" in mods:
        rate = 1.5
    elif "HT" in mods:
        rate = 0.75
    if rate != 1.0:
        ar_ms = preempt(ar) / rate
        ar = 5 + (1200 - ar_ms) * 5 / 750 if ar_ms <= 1200 else 5 - (ar_ms - 1200) * 5 / 600
        window = (80 - 6 * od) / rate
        od = (80 - window) / 6
    return cs, ar, od, rate, "HR" in mods

def _percentile(values: list[float], share: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    spot = (len(ordered) - 1) * share
    low, high = math.floor(spot), math.ceil(spot)
    return ordered[low] + (ordered[high] - ordered[low]) * (spot - low)

def _summary(values: list[float]) -> dict[str, float]:
    return {"median": median(values) if values else 0.0, "p90": _percentile(values, 0.9), "p99": _percentile(values, 0.99),
            "max": max(values) if values else 0.0}

def _angle(a, b, c) -> float:
    first = (a[0] - b[0], a[1] - b[1])
    second = (c[0] - b[0], c[1] - b[1])
    lengths = math.hypot(*first) * math.hypot(*second)
    if lengths < 1e-6:
        return 0.0
    cosine = max(-1.0, min(1.0, (first[0] * second[0] + first[1] * second[1]) / lengths))
    return math.degrees(math.acos(cosine))

def _end(item: HitObject, flip: bool) -> tuple[float, float]:
    if item.kind == "slider" and item.path:
        x, y = item.path[-1] if item.repeats % 2 == 1 else item.path[0]
    else:
        x, y = item.x, item.y
    return (x, 384 - y) if flip else (x, y)

def _start(item: HitObject, flip: bool) -> tuple[float, float]:
    return (item.x, 384 - item.y) if flip else (item.x, item.y)

def streams(times: list[float]) -> list[int]:
    chains: list[int] = []
    current: list[float] = times[:1]
    for moment in times[1:]:
        trial = current + [moment]
        gaps = [b - a for a, b in zip(trial, trial[1:])]
        middle = median(gaps)
        if middle > 0 and all(abs(gap - middle) <= STREAM_SPREAD * middle for gap in gaps):
            current = trial
            continue
        if len(current) >= STREAM_LEAST:
            chains.append(len(current))
        current = [current[-1], moment]
    if len(current) >= STREAM_LEAST:
        chains.append(len(current))
    return chains

def windows(moments: list[float], loads: list[float], length: float, size: float) -> dict[str, float]:
    if not moments:
        return {"median": 0.0, "p90": 0.0, "p99": 0.0, "max": 0.0, "loaded_share": 0.0, "longest_rest": 0.0}
    step = size / 2
    values, start = [], moments[0]
    while start < moments[-1] + 1e-9:
        end = start + size
        inside = [load for moment, load in zip(moments, loads) if start <= moment < end]
        values.append(sum(inside) / size)
        start += step
    peak = max(values) if values else 0.0
    loaded = [value >= LOADED * peak for value in values] if peak > 0 else []
    longest, current = 0, 0
    for busy in loaded:
        current = 0 if busy else current + 1
        longest = max(longest, current)
    found = _summary(values)
    found["loaded_share"] = sum(loaded) / len(loaded) if loaded else 0.0
    found["longest_rest"] = longest * step
    return found

def analyse(beatmap: Beatmap, mods: str = "") -> dict[str, Any]:
    cs, ar, od, rate, flip = apply_mods(beatmap, mods)
    size = radius(cs)
    items = [item for item in beatmap.objects if item.kind != "spinner"]
    times = [item.time / rate for item in items]
    seconds = [moment / 1000 for moment in times]
    gaps, distances, spacing, angles, speeds = [], [], [], [], []
    for index in range(1, len(items)):
        gap = max(1.0, times[index] - (items[index - 1].end_time / rate))
        jump = math.dist(_end(items[index - 1], flip), _start(items[index], flip))
        gaps.append(times[index] - times[index - 1])
        distances.append(jump)
        spacing.append(jump / (2 * size))
        speeds.append(jump / gap)
        if index >= 2:
            angles.append(_angle(_end(items[index - 2], flip), _start(items[index - 1], flip), _start(items[index], flip)))
    circles = [items[i].time / rate for i in range(len(items)) if items[i].kind == "circle"]
    chains = streams(circles)
    bins = {f"{low}-{high or 'plus'}": sum(1 for length in chains if length >= low and (high is None or length <= high))
            for low, high in STREAM_BINS}
    window_ms = preempt(ar)
    visible = []
    for index, moment in enumerate(times):
        visible.append(sum(1 for other in times[index:index + 40] if other - moment < window_ms))
    overlaps = sum(1 for index in range(1, len(items)) if distances[index - 1] < 2 * size and gaps[index - 1] > window_ms / 2)
    sliders = [item for item in items if item.kind == "slider"]
    slider_speed = [item.length * item.repeats / max(1.0, (item.end_time - item.time) / rate) for item in sliders]
    length = (seconds[-1] - seconds[0]) if len(seconds) > 1 else 0.0
    aim_load = [0.0] + [speed for speed in speeds]
    tap_load = [0.0] + [1000 / max(1.0, gap) for gap in gaps]
    rhythm = [abs(math.log(gaps[i] / gaps[i - 1])) for i in range(1, len(gaps)) if gaps[i] > 0 and gaps[i - 1] > 0]
    return {
        "version": FEATURE_VERSION,
        "mods": mods,
        "effective": {"cs": round(cs, 3), "ar": round(ar, 3), "od": round(od, 3), "radius": round(size, 3), "rate": rate},
        "objects": len(items),
        "length_seconds": round(length, 3),
        "interval_ms": _summary(gaps),
        "distance_px": _summary(distances),
        "spacing_radii": _summary(spacing),
        "angle_deg": _summary(angles),
        "speed_px_ms": _summary(speeds),
        "rhythm_change": _summary(rhythm),
        "streams": {"chains": len(chains), "objects": sum(chains), "share": sum(chains) / max(1, len(circles)), "lengths": bins},
        "reading": {"visible": _summary([float(v) for v in visible]), "preempt_ms": round(window_ms, 1), "overlaps": overlaps},
        "sliders": {"share": len(sliders) / max(1, len(items)), "speed": _summary(slider_speed),
                    "repeats": sum(item.repeats - 1 for item in sliders)},
        "aim_windows": {f"{int(size_s)}s": windows(seconds, aim_load, length, size_s) for size_s in WINDOWS},
        "tap_windows": {f"{int(size_s)}s": windows(seconds, tap_load, length, size_s) for size_s in WINDOWS},
    }
