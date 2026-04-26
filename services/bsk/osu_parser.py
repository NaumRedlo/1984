"""
.osu file parser for BSK map feature extraction.

Analyses hitobject patterns to produce skill-relevant features:
  stream_density      — overall fraction of objects inside fast runs
  burst_density       — fraction inside 2–4 note bursts
  full_stream_density — fraction inside 5–15 note streams
  death_stream_density— fraction inside 16+ note streams (deathstreams)
  jump_density        — fraction of intervals that are "jump" gaps (>150 px, >110 ms)
  avg_jump_velocity   — normalised mean distance/time for jump sections  (0–1)
  back_forth_ratio    — fraction of angles > 135° (direction reversals)
  angle_variance      — normalised std-dev of movement angles (0–1)
  slider_density      — fraction of objects that are sliders
  sv_variance         — normalised std-dev of inherited SV changes (tech maps)
  rhythm_complexity   — coefficient of variation of note intervals (0–1)
  density_variance    — normalised std-dev of per-second note density (0–1)
  note_count          — total hitobjects
  duration_seconds    — map active duration in seconds
"""

import math
from typing import Optional


# ─── Hit-object / timing-point parsers ───────────────────────────────────────

def _parse_hitobjects(osu_text: str) -> list[dict]:
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
                x        = float(parts[0])
                y        = float(parts[1])
                t        = int(parts[2])
                obj_type = int(parts[3])
                objects.append({
                    "x":       x,
                    "y":       y,
                    "t":       t,
                    "slider":  bool(obj_type & 2),
                    "spinner": bool(obj_type & 8),
                })
            except (ValueError, IndexError):
                continue
    return objects


def _parse_timing_points(osu_text: str) -> list[dict]:
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
                t          = int(parts[0])
                beat_len   = float(parts[1])
                uninherited = int(parts[6]) if len(parts) > 6 else 1
                points.append({"t": t, "beat_len": beat_len, "uninherited": uninherited})
            except (ValueError, IndexError):
                continue
    return points


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _dist(a: dict, b: dict) -> float:
    return math.sqrt((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2)


def _find_stream_runs(intervals: list[float], threshold: int = 110) -> list[int]:
    """
    Return a list of note-counts for each consecutive run of intervals all
    below `threshold` ms.  A run of k intervals = k+1 notes.
    """
    runs: list[int] = []
    run_len = 0
    for dt in intervals:
        if dt < threshold:
            run_len += 1
        else:
            if run_len >= 1:
                runs.append(run_len + 1)
            run_len = 0
    if run_len >= 1:
        runs.append(run_len + 1)
    return runs


def _sv_variance(timing_points: list[dict]) -> float:
    """Normalised variance of slider velocity multipliers from inherited points."""
    sv_vals = []
    for tp in timing_points:
        if not tp["uninherited"] and tp["beat_len"] < 0:
            sv = min(-100.0 / tp["beat_len"], 10.0)  # cap wild outliers
            sv_vals.append(sv)
    if len(sv_vals) < 2:
        return 0.0
    mean_sv = sum(sv_vals) / len(sv_vals)
    std_sv  = math.sqrt(sum((sv - mean_sv) ** 2 for sv in sv_vals) / len(sv_vals))
    return min(std_sv / (mean_sv + 1e-9), 1.0)


def _empty_features(n: int) -> dict:
    return {
        "stream_density":        0.0,
        "burst_density":         0.0,
        "full_stream_density":   0.0,
        "death_stream_density":  0.0,
        "jump_density":          0.0,
        "avg_jump_velocity":     0.0,
        "back_forth_ratio":      0.0,
        "angle_variance":        0.0,
        "slider_density":        0.0,
        "sv_variance":           0.0,
        "rhythm_complexity":     0.0,
        "density_variance":      0.0,
        "note_count":            n,
        "duration_seconds":      0,
    }


# ─── Main feature extractor ───────────────────────────────────────────────────

def extract_features(osu_text: str) -> dict:
    """
    Parse a .osu file and return a feature dict usable by weights_from_features().
    Pure Python, no external dependencies.
    """
    objects = _parse_hitobjects(osu_text)
    timing_points = _parse_timing_points(osu_text)

    if len(objects) < 2:
        return _empty_features(len(objects))

    n = len(objects)
    duration_ms = objects[-1]["t"] - objects[0]["t"]
    duration_s  = max(duration_ms / 1000.0, 1.0)

    # Pre-compute intervals and distances
    intervals: list[float] = []
    distances: list[float] = []
    for i in range(n - 1):
        dt = objects[i + 1]["t"] - objects[i]["t"]
        if dt > 0:
            intervals.append(float(dt))
        else:
            intervals.append(1.0)  # guard against simultaneous notes
        distances.append(_dist(objects[i], objects[i + 1]))

    n_iv = len(intervals)

    # ── Stream analysis ──────────────────────────────────────────────────────
    runs = _find_stream_runs(intervals, threshold=110)
    total_burst       = sum(r for r in runs if 2 <= r <= 4)
    total_stream      = sum(r for r in runs if 5 <= r <= 15)
    total_deathstream = sum(r for r in runs if r > 15)
    total_any_stream  = total_burst + total_stream + total_deathstream

    burst_density        = total_burst        / n
    full_stream_density  = total_stream       / n
    death_stream_density = total_deathstream  / n
    stream_density       = total_any_stream   / n

    # ── Jump analysis ────────────────────────────────────────────────────────
    # A "jump" is a non-stream interval (≥110 ms) with noticeable distance (>150 px)
    jump_velocities: list[float] = []
    jump_count = 0
    for dt, d in zip(intervals, distances):
        if dt >= 110 and d > 150:
            jump_count += 1
            jump_velocities.append(d / dt)

    jump_density       = jump_count / n_iv if n_iv else 0.0
    avg_jump_velocity  = sum(jump_velocities) / len(jump_velocities) if jump_velocities else 0.0
    # 3 px/ms ≈ very hard jump (normalize to [0,1])
    avg_jump_velocity  = min(avg_jump_velocity / 3.0, 1.0)

    # ── Angle analysis ───────────────────────────────────────────────────────
    angles: list[float] = []
    for i in range(1, n - 1):
        dx1 = objects[i]["x"]     - objects[i-1]["x"]
        dy1 = objects[i]["y"]     - objects[i-1]["y"]
        dx2 = objects[i+1]["x"]   - objects[i]["x"]
        dy2 = objects[i+1]["y"]   - objects[i]["y"]
        len1 = math.sqrt(dx1**2 + dy1**2)
        len2 = math.sqrt(dx2**2 + dy2**2)
        if len1 > 5 and len2 > 5:
            cos_a = (dx1*dx2 + dy1*dy2) / (len1 * len2)
            cos_a = max(-1.0, min(1.0, cos_a))
            angles.append(math.acos(cos_a))

    back_forth_ratio = 0.0
    angle_variance   = 0.0
    if angles:
        # Direction reversal: angle > 135° (≈ 2.36 rad)
        back_forth_ratio = sum(1 for a in angles if a > 2.36) / len(angles)
        mean_a = sum(angles) / len(angles)
        std_a  = math.sqrt(sum((a - mean_a) ** 2 for a in angles) / len(angles))
        angle_variance = min(std_a / (math.pi / 2), 1.0)

    # ── Slider / SV analysis ─────────────────────────────────────────────────
    slider_count  = sum(1 for o in objects if o["slider"])
    slider_density = slider_count / n
    sv_var         = _sv_variance(timing_points)

    # ── Rhythm complexity ────────────────────────────────────────────────────
    if intervals:
        mean_dt = sum(intervals) / n_iv
        std_dt  = math.sqrt(sum((dt - mean_dt) ** 2 for dt in intervals) / n_iv)
        rhythm_complexity = min(std_dt / (mean_dt + 1e-9), 1.0)
    else:
        rhythm_complexity = 0.0

    # ── Note density variance (per-second windows) ───────────────────────────
    density_variance = 0.0
    if duration_s > 2:
        t0        = objects[0]["t"]
        n_windows = max(1, int(duration_s))
        counts    = [0] * n_windows
        for obj in objects:
            w = min(int((obj["t"] - t0) / 1000), n_windows - 1)
            counts[w] += 1
        mean_c = sum(counts) / len(counts)
        std_c  = math.sqrt(sum((c - mean_c) ** 2 for c in counts) / len(counts))
        density_variance = min(std_c / (mean_c + 1e-9), 1.0)

    return {
        "stream_density":        round(stream_density, 4),
        "burst_density":         round(burst_density, 4),
        "full_stream_density":   round(full_stream_density, 4),
        "death_stream_density":  round(death_stream_density, 4),
        "jump_density":          round(jump_density, 4),
        "avg_jump_velocity":     round(avg_jump_velocity, 4),
        "back_forth_ratio":      round(back_forth_ratio, 4),
        "angle_variance":        round(angle_variance, 4),
        "slider_density":        round(slider_density, 4),
        "sv_variance":           round(sv_var, 4),
        "rhythm_complexity":     round(rhythm_complexity, 4),
        "density_variance":      round(density_variance, 4),
        "note_count":            n,
        "duration_seconds":      int(duration_s),
    }


# ─── Weight computation ───────────────────────────────────────────────────────

def weights_from_features(
    features: dict,
    bpm: float = 0.0,
    ar:  float = 0.0,
    od:  float = 0.0,
    *,
    api_aim:          float = 0.0,
    api_speed:        float = 0.0,
    api_slider_factor: float = 1.0,
) -> dict:
    """
    Compute BSK skill weights from parsed features + metadata + optional
    osu! API difficulty attributes.

    When osu! API attributes (api_aim, api_speed) are available they dominate
    the aim/speed split with 60% weight; pure-feature signals fill the rest.

    Returns {aim, speed, acc, cons} summing to 1.0.
    """
    burst   = features.get("burst_density",        0.0)
    fstream = features.get("full_stream_density",  0.0)
    death   = features.get("death_stream_density", 0.0)
    jd      = features.get("jump_density",         0.0)
    jv      = features.get("avg_jump_velocity",    0.0)
    bf      = features.get("back_forth_ratio",     0.0)
    av      = features.get("angle_variance",       0.0)
    sld     = features.get("slider_density",       0.0)
    sv_var  = features.get("sv_variance",          0.0)
    rc      = features.get("rhythm_complexity",    0.0)
    dv      = features.get("density_variance",     0.0)
    dur     = features.get("duration_seconds",     0)

    bpm_norm = min(bpm / 200.0, 1.0) if bpm > 0 else 0.5
    ar_norm  = min(ar  / 10.0,  1.0) if ar  > 0 else 0.5
    od_norm  = min(od  / 10.0,  1.0) if od  > 0 else 0.5
    len_norm = min(dur / 300.0, 1.0) if dur > 0 else 0.5

    # ── Feature-based signals ────────────────────────────────────────────────
    # Speed: stream density weighted by intensity, supported by BPM
    speed_feat = (
        0.20 * burst +
        0.45 * fstream +
        0.35 * death
    ) if (burst + fstream + death) > 0.01 else 0.0
    speed_feat = 0.65 * speed_feat + 0.35 * bpm_norm

    # Aim: jumps (density × velocity) + directional complexity + high AR
    aim_feat = (
        0.30 * jd +
        0.30 * jv +
        0.25 * bf +       # back-and-forth = jump aim style
        0.15 * ar_norm
    )

    # Accuracy: OD tightness + slider content + stable SV changes
    # High sv_variance → tech sliders require reading + precision
    # Low rhythm_complexity → predictable beat → pure accuracy test
    acc_feat = (
        0.40 * od_norm +
        0.25 * sld +
        0.25 * sv_var +
        0.10 * (1.0 - rc)
    )

    # Consistency: length + density variance (uneven map = stamina test)
    # + angle_variance (complex movement = sustained skill)
    cons_feat = (
        0.50 * len_norm +
        0.30 * dv +
        0.20 * av
    )

    # ── Blend with osu! API attributes (much more accurate aim/speed split) ──
    if api_aim > 0.0 and api_speed > 0.0:
        api_aim_n  = min(api_aim   / 8.0, 1.0)
        api_spd_n  = min(api_speed / 8.0, 1.0)
        # Low slider_factor = sliders dominate aim → push acc slightly
        slider_acc = (1.0 - max(api_slider_factor, 0.0)) * 0.15

        aim_raw   = 0.40 * aim_feat   + 0.60 * api_aim_n
        speed_raw = 0.40 * speed_feat + 0.60 * api_spd_n
        acc_raw   = acc_feat + slider_acc
        cons_raw  = cons_feat
    else:
        aim_raw   = aim_feat
        speed_raw = speed_feat
        acc_raw   = acc_feat
        cons_raw  = cons_feat

    raw   = {"aim": aim_raw, "speed": speed_raw, "acc": acc_raw, "cons": cons_raw}
    total = sum(raw.values()) or 1.0
    return {k: round(v / total, 3) for k, v in raw.items()}


def map_type_from_weights(weights: dict) -> str:
    return max(weights, key=weights.get)
