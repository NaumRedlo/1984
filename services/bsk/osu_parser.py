"""
.osu file parser for BSK map feature extraction.
Computes stream_density, jump_density, slider_density, rhythm_complexity
from raw hitobject data without external dependencies.
"""

import math
import re
from io import BytesIO
from typing import Optional


def _parse_hitobjects(osu_text: str) -> list[dict]:
    """Extract hitobjects from .osu file text."""
    objects = []
    in_section = False
    for line in osu_text.splitlines():
        line = line.strip()
        if line == "[HitObjects]":
            in_section = True
            continue
        if in_section:
            if line.startswith("["):
                break
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 5:
                continue
            try:
                x = float(parts[0])
                y = float(parts[1])
                t = int(parts[2])
                obj_type = int(parts[3])
                is_slider = bool(obj_type & 2)
                is_spinner = bool(obj_type & 8)
                objects.append({"x": x, "y": y, "t": t, "slider": is_slider, "spinner": is_spinner})
            except (ValueError, IndexError):
                continue
    return objects


def _parse_timing_points(osu_text: str) -> list[dict]:
    """Extract timing points to get BPM."""
    points = []
    in_section = False
    for line in osu_text.splitlines():
        line = line.strip()
        if line == "[TimingPoints]":
            in_section = True
            continue
        if in_section:
            if line.startswith("["):
                break
            if not line:
                continue
            parts = line.split(",")
            if len(parts) < 2:
                continue
            try:
                t = int(parts[0])
                beat_len = float(parts[1])
                uninherited = int(parts[6]) if len(parts) > 6 else 1
                points.append({"t": t, "beat_len": beat_len, "uninherited": uninherited})
            except (ValueError, IndexError):
                continue
    return points


def _dist(a: dict, b: dict) -> float:
    return math.sqrt((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2)


def extract_features(osu_text: str) -> dict:
    """
    Extract BSK skill features from .osu file text.

    Returns dict with:
      stream_density, jump_density, slider_density, rhythm_complexity,
      note_count, duration_seconds
    """
    objects = _parse_hitobjects(osu_text)
    if len(objects) < 2:
        return {
            "stream_density": 0.0,
            "jump_density": 0.0,
            "slider_density": 0.0,
            "rhythm_complexity": 0.0,
            "note_count": len(objects),
            "duration_seconds": 0,
        }

    n = len(objects)
    duration_ms = objects[-1]["t"] - objects[0]["t"]
    duration_s = max(duration_ms / 1000, 1)

    intervals = []
    distances = []
    for i in range(n - 1):
        dt = objects[i + 1]["t"] - objects[i]["t"]
        if dt > 0:
            intervals.append(dt)
        distances.append(_dist(objects[i], objects[i + 1]))

    # Stream: consecutive notes with interval < 110ms (roughly 1/4 at 136+ BPM)
    stream_count = sum(1 for dt in intervals if dt < 110)
    stream_density = stream_count / len(intervals) if intervals else 0.0

    # Jump: notes with distance > 200 osu!pixels
    jump_count = sum(1 for d in distances if d > 200)
    jump_density = jump_count / len(distances) if distances else 0.0

    # Slider density
    slider_count = sum(1 for o in objects if o["slider"])
    slider_density = slider_count / n

    # Rhythm complexity: coefficient of variation of intervals
    if intervals:
        mean_dt = sum(intervals) / len(intervals)
        variance = sum((dt - mean_dt) ** 2 for dt in intervals) / len(intervals)
        std_dt = math.sqrt(variance)
        rhythm_complexity = min(std_dt / (mean_dt + 1e-9), 1.0)
    else:
        rhythm_complexity = 0.0

    return {
        "stream_density": round(stream_density, 4),
        "jump_density": round(jump_density, 4),
        "slider_density": round(slider_density, 4),
        "rhythm_complexity": round(rhythm_complexity, 4),
        "note_count": n,
        "duration_seconds": int(duration_s),
    }


def weights_from_features(features: dict, bpm: float = 0, ar: float = 0, od: float = 0) -> dict:
    """
    Compute BSK skill weights from extracted features + metadata.
    Returns {aim, speed, acc, cons} summing to 1.0.
    """
    sd = features.get("stream_density", 0.0)
    jd = features.get("jump_density", 0.0)
    sld = features.get("slider_density", 0.0)
    rc = features.get("rhythm_complexity", 0.0)
    dur = features.get("duration_seconds", 0)

    # Blend feature-based signals with metadata signals
    # Speed: streams + high BPM
    speed_raw = 0.6 * sd + 0.4 * min(bpm / 300.0, 1.0) if bpm else sd

    # Aim: jumps + high AR (fast approach = harder aim)
    aim_raw = 0.6 * jd + 0.4 * min(ar / 10.0, 1.0) if ar else jd

    # Accuracy: sliders + high OD (tight hit window)
    acc_raw = 0.5 * sld + 0.5 * min(od / 10.0, 1.0) if od else sld

    # Consistency: rhythm complexity + long duration
    cons_raw = 0.5 * rc + 0.5 * min(dur / 300.0, 1.0)

    raw = {"aim": aim_raw, "speed": speed_raw, "acc": acc_raw, "cons": cons_raw}
    total = sum(raw.values()) or 1.0
    return {k: round(v / total, 3) for k, v in raw.items()}


def map_type_from_weights(weights: dict) -> str:
    return max(weights, key=weights.get)
