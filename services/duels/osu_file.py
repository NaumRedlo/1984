import hashlib
import math
from dataclasses import dataclass, field
from typing import Optional

FILE_MOST = 4 * 1024 * 1024
OBJECTS_MOST = 20000
PLAYFIELD = (512, 384)
CIRCLE = 1
SLIDER = 2
SPINNER = 8

class OsuFileError(ValueError):
    pass

@dataclass
class Timing:
    time: float
    beat_length: float
    uninherited: bool

@dataclass
class HitObject:
    x: float
    y: float
    time: float
    kind: str
    new_combo: bool
    end_time: float
    path: list[tuple[float, float]] = field(default_factory=list)
    repeats: int = 1
    length: float = 0.0

@dataclass
class Beatmap:
    format_version: int
    mode: int
    cs: float
    ar: float
    od: float
    hp: float
    slider_multiplier: float
    slider_tick_rate: float
    timing: list[Timing]
    objects: list[HitObject]
    digest: str
    creator: str = ""
    beatmap_id: Optional[int] = None
    title: str = ""
    artist: str = ""
    version: str = ""

def _number(value: str, name: str) -> float:
    try:
        number = float(value)
    except ValueError:
        raise OsuFileError(f"bad_number:{name}")
    if not math.isfinite(number):
        raise OsuFileError(f"bad_number:{name}")
    return number

def _sections(text: str) -> tuple[int, dict[str, list[str]]]:
    lines = text.replace("﻿", "").splitlines()
    if not lines or not lines[0].strip().startswith("osu file format v"):
        raise OsuFileError("not_osu_file")
    try:
        version = int(lines[0].strip().rsplit("v", 1)[1])
    except ValueError:
        raise OsuFileError("bad_version")
    sections: dict[str, list[str]] = {}
    current = None
    for raw in lines[1:]:
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            sections.setdefault(current, [])
        elif current is not None:
            sections[current].append(line)
    return version, sections

def _pairs(lines: list[str]) -> dict[str, str]:
    found = {}
    for line in lines:
        key, sep, value = line.partition(":")
        if sep:
            found[key.strip()] = value.strip()
    return found

def _bezier(points: list[tuple[float, float]], steps: int = 24) -> list[tuple[float, float]]:
    found = []
    for step in range(steps + 1):
        t = step / steps
        layer = list(points)
        while len(layer) > 1:
            layer = [((1 - t) * a[0] + t * b[0], (1 - t) * a[1] + t * b[1]) for a, b in zip(layer, layer[1:])]
        found.append(layer[0])
    return found

def _arc(a, b, c, steps: int = 32) -> Optional[list[tuple[float, float]]]:
    d = 2 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-6:
        return None
    ux = ((a[0] ** 2 + a[1] ** 2) * (b[1] - c[1]) + (b[0] ** 2 + b[1] ** 2) * (c[1] - a[1]) + (c[0] ** 2 + c[1] ** 2) * (a[1] - b[1])) / d
    uy = ((a[0] ** 2 + a[1] ** 2) * (c[0] - b[0]) + (b[0] ** 2 + b[1] ** 2) * (a[0] - c[0]) + (c[0] ** 2 + c[1] ** 2) * (b[0] - a[0])) / d
    radius = math.hypot(a[0] - ux, a[1] - uy)
    start = math.atan2(a[1] - uy, a[0] - ux)
    middle = math.atan2(b[1] - uy, b[0] - ux)
    end = math.atan2(c[1] - uy, c[0] - ux)
    def turn(x):
        return (x - start) % (2 * math.pi)
    sweep = turn(end)
    if turn(middle) > sweep:
        sweep -= 2 * math.pi
    return [(ux + radius * math.cos(start + sweep * k / steps), uy + radius * math.sin(start + sweep * k / steps)) for k in range(steps + 1)]

def _segments(points: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    parts, current = [], [points[0]]
    for point in points[1:]:
        if point == current[-1]:
            parts.append(current)
            current = [point]
        else:
            current.append(point)
    parts.append(current)
    return [part for part in parts if len(part) > 1]

def slider_path(kind: str, points: list[tuple[float, float]], length: float) -> list[tuple[float, float]]:
    if kind == "P" and len(points) == 3:
        arc = _arc(*points)
        traced = arc if arc is not None else points
    elif kind == "L":
        traced = points
    else:
        traced = []
        for part in _segments(points):
            curve = _bezier(part) if len(part) > 2 else part
            traced.extend(curve if not traced else curve[1:])
    out, walked = [traced[0]], 0.0
    for a, b in zip(traced, traced[1:]):
        step = math.hypot(b[0] - a[0], b[1] - a[1])
        if length > 0 and walked + step >= length:
            share = (length - walked) / step if step else 0.0
            out.append((a[0] + (b[0] - a[0]) * share, a[1] + (b[1] - a[1]) * share))
            return out
        walked += step
        out.append(b)
    return out

def _beat_at(timing: list[Timing], time: float) -> tuple[float, float]:
    beat, velocity = timing[0].beat_length, 1.0
    for point in timing:
        if point.time > time + 1e-6:
            break
        if point.uninherited:
            beat, velocity = point.beat_length, 1.0
        elif point.beat_length < 0:
            velocity = min(10.0, max(0.1, -100.0 / point.beat_length))
    return beat, velocity

def parse(raw: bytes) -> Beatmap:
    if len(raw) > FILE_MOST:
        raise OsuFileError("too_large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise OsuFileError("not_utf8")
    version, sections = _sections(text)
    general = _pairs(sections.get("General", []))
    difficulty = _pairs(sections.get("Difficulty", []))
    metadata = _pairs(sections.get("Metadata", []))
    mode = int(_number(general.get("Mode", "0"), "Mode"))
    values = {}
    for name in ("CircleSize", "OverallDifficulty", "HPDrainRate"):
        values[name] = _number(difficulty.get(name, "5"), name)
    values["ApproachRate"] = _number(difficulty.get("ApproachRate", difficulty.get("OverallDifficulty", "5")), "ApproachRate")
    for name, value in values.items():
        if not 0 <= value <= 10:
            raise OsuFileError(f"out_of_range:{name}")
    multiplier = _number(difficulty.get("SliderMultiplier", "1.4"), "SliderMultiplier")
    tick_rate = _number(difficulty.get("SliderTickRate", "1"), "SliderTickRate")
    if not 0.4 <= multiplier <= 3.6 or not 0 < tick_rate <= 8:
        raise OsuFileError("out_of_range:Slider")
    timing = []
    for line in sections.get("TimingPoints", []):
        fields = line.split(",")
        if len(fields) < 2:
            raise OsuFileError("bad_timing")
        moment, length = _number(fields[0], "timing"), _number(fields[1], "timing")
        uninherited = fields[6].strip() != "0" if len(fields) > 6 else True
        if uninherited and length <= 0:
            raise OsuFileError("bad_timing")
        timing.append(Timing(moment, length, uninherited))
    timing.sort(key=lambda point: (point.time, not point.uninherited))
    if not any(point.uninherited for point in timing):
        raise OsuFileError("no_timing")
    objects = []
    for line in sections.get("HitObjects", []):
        fields = line.split(",")
        if len(fields) < 4:
            raise OsuFileError("bad_object")
        x, y, time = _number(fields[0], "x"), _number(fields[1], "y"), _number(fields[2], "time")
        kind_bits = int(_number(fields[3], "type"))
        if time < 0 or not (-PLAYFIELD[0] <= x <= 2 * PLAYFIELD[0]) or not (-PLAYFIELD[1] <= y <= 2 * PLAYFIELD[1]):
            raise OsuFileError("bad_object")
        if objects and time < objects[-1].time:
            raise OsuFileError("objects_out_of_order")
        new_combo = bool(kind_bits & 4)
        if kind_bits & SLIDER:
            if len(fields) < 8:
                raise OsuFileError("bad_slider")
            curve = fields[5].split("|")
            points = [(x, y)]
            for pair in curve[1:]:
                px, _, py = pair.partition(":")
                points.append((_number(px, "curve"), _number(py, "curve")))
            repeats = int(_number(fields[6], "slides"))
            length = _number(fields[7], "length")
            if repeats < 1 or length < 0 or len(points) < 2:
                raise OsuFileError("bad_slider")
            beat, velocity = _beat_at(timing, time)
            duration = length / (multiplier * 100 * velocity) * beat * repeats
            objects.append(HitObject(x, y, time, "slider", new_combo, time + duration, slider_path(curve[0], points, length),
                                     repeats, length))
        elif kind_bits & SPINNER:
            end = _number(fields[5], "spinner") if len(fields) > 5 else time
            objects.append(HitObject(256, 192, time, "spinner", new_combo, max(time, end)))
        elif kind_bits & CIRCLE:
            objects.append(HitObject(x, y, time, "circle", new_combo, time))
        else:
            raise OsuFileError("bad_object")
        if len(objects) > OBJECTS_MOST:
            raise OsuFileError("too_many_objects")
    if not objects:
        raise OsuFileError("no_objects")
    beatmap_id = metadata.get("BeatmapID")
    return Beatmap(version, mode, values["CircleSize"], values["ApproachRate"], values["OverallDifficulty"], values["HPDrainRate"],
                   multiplier, tick_rate, timing, objects, hashlib.md5(raw).hexdigest(), metadata.get("Creator", ""),
                   int(beatmap_id) if beatmap_id and beatmap_id.isdigit() else None,
                   metadata.get("Title", ""), metadata.get("Artist", ""), metadata.get("Version", ""))
