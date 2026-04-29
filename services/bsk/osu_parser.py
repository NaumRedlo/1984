"""
.osu file parser for BSK skill metric extraction.

PHASE 2 — overhaul.  Four metrics redefined as orthogonal axes:
    AIM   — spatial precision (where to click and how far)
    SPEED — tempo density   (how often to tap, BPM-relative)
    ACC   — temporal precision (when to click each note)
    CONS  — endurance / sustained intensity (how long to hold all that)

Output is twofold:
    1. Per-skill ABSOLUTE STARS in [0..10] (independent — a map can be
       8★ aim AND 8★ acc).  Used for matchmaking + classification.
    2. Share-weights summing to 1.0 derived via softmax(stars/T) — kept
       for legacy UI & ML compatibility.

Public API (used across the codebase):
    extract_features(osu_text)                   — parsed feature dict
    compute_skill_intrinsics(features, …)        — intrinsic [0..1] per skill
    compute_skill_stars(features, …, sr, …)      — absolute stars [0..10]
    stars_to_weights(stars, temperature=2.0)     — softmax → shares
    map_type_from_stars(stars)                   — argmax key
    weights_from_features(features, …)           — legacy, returns shares
    map_type_from_weights(weights)               — legacy, argmax key
"""

import math
from typing import Optional


# ─── Hit-object / timing-point parsers ───────────────────────────────────────

def _parse_hitobjects(osu_text: str) -> list[dict]:
    """Parse [HitObjects] including slider params (length, repeats)."""
    objects: list[dict] = []
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
                obj = {
                    "x":       x,
                    "y":       y,
                    "t":       t,
                    "circle":  bool(obj_type & 1),
                    "slider":  bool(obj_type & 2),
                    "spinner": bool(obj_type & 8),
                    "repeats": 1,
                    "length":  0.0,
                }
                # Slider params: parts[5]=path, parts[6]=repeats, parts[7]=length
                if obj["slider"] and len(parts) >= 8:
                    try:
                        obj["repeats"] = max(1, int(parts[6]))
                    except (ValueError, IndexError):
                        obj["repeats"] = 1
                    try:
                        obj["length"] = max(0.0, float(parts[7]))
                    except (ValueError, IndexError):
                        obj["length"] = 0.0
                objects.append(obj)
            except (ValueError, IndexError):
                continue
    return objects


def _parse_timing_points(osu_text: str) -> list[dict]:
    """Parse [TimingPoints]; uninherited points carry beat_len, inherited carry SV."""
    points: list[dict] = []
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
                t           = int(float(parts[0]))
                beat_len    = float(parts[1])
                uninherited = int(parts[6]) if len(parts) > 6 else 1
                points.append({"t": t, "beat_len": beat_len, "uninherited": uninherited})
            except (ValueError, IndexError):
                continue
    return points


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _dist(a: dict, b: dict) -> float:
    return math.sqrt((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2)


def _build_beat_lookup(timing_points: list[dict]) -> list[tuple[int, float]]:
    """Sorted list [(offset, beat_len), ...] of uninherited points only."""
    return sorted(
        [(tp["t"], tp["beat_len"]) for tp in timing_points if tp["uninherited"] and tp["beat_len"] > 0],
        key=lambda x: x[0],
    )


def _beat_at(t: int, uninherited: list[tuple[int, float]]) -> tuple[int, float]:
    """Return (offset, beat_len) active at time t.  Defaults to 120 BPM."""
    if not uninherited:
        return (0, 500.0)
    last = uninherited[0]
    for tp in uninherited:
        if tp[0] > t:
            break
        last = tp
    return last


def _classify_subdivision(interval_ms: float, beat_len: float) -> Optional[str]:
    """Snap a note interval to a standard beat subdivision name.
    Returns None if interval invalid; 'other' if it doesn't snap to any standard."""
    if interval_ms <= 0 or beat_len <= 0:
        return None
    ratio = beat_len / interval_ms          # notes per beat
    # Standard subdivisions (notes per beat)
    candidates = [
        (0.5,  "1/2_slow"),  # half-note interval (slow)
        (1.0,  "1/1"),        # quarter
        (2.0,  "1/2"),        # eighth
        (3.0,  "1/3"),        # triplet
        (4.0,  "1/4"),        # sixteenth
        (6.0,  "1/6"),        # sextuplet
        (8.0,  "1/8"),        # 32nd
        (16.0, "1/16"),       # 64th
    ]
    best_name, best_err = None, 1e9
    for target, name in candidates:
        # Relative error
        err = abs(ratio - target) / target
        if err < best_err:
            best_err = err
            best_name = name
    if best_err > 0.10:                       # >10% off any standard → custom rhythm
        return "other"
    return best_name


def _find_stream_runs(intervals: list[float], threshold: int = 110) -> list[int]:
    """Return note-counts of each consecutive run with all gaps below `threshold` ms."""
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
    """Std-dev of slider-velocity multipliers from inherited points (negative beat_len)."""
    sv_vals = []
    for tp in timing_points:
        if not tp["uninherited"] and tp["beat_len"] < 0:
            sv = min(-100.0 / tp["beat_len"], 10.0)
            sv_vals.append(sv)
    if len(sv_vals) < 2:
        return 0.0
    mean_sv = sum(sv_vals) / len(sv_vals)
    std_sv  = math.sqrt(sum((sv - mean_sv) ** 2 for sv in sv_vals) / len(sv_vals))
    return min(std_sv / (mean_sv + 1e-9), 1.0)


# ─── New per-skill feature extractors ────────────────────────────────────────

def _subdivision_features(
    objects: list[dict],
    uninherited: list[tuple[int, float]],
) -> tuple[float, float, float]:
    """
    Returns (entropy_norm, polyrhythm_density, off_beat_ratio).

    entropy_norm        — Shannon entropy of subdivision usage, normalized to log(8)
    polyrhythm_density  — fraction of 4s windows containing 2+ distinct *uncommon*
                          subdivisions (anything except the 1/1 and 1/4 grid)
    off_beat_ratio      — mean snap distance from 1/4 grid, normalized to 0.5
                          (0 = perfectly on beat, ~1 = halfway between beats)
    """
    if len(objects) < 2:
        return 0.0, 0.0, 0.0

    subdivs: list[str] = []
    off_beat_distances: list[float] = []
    for i in range(1, len(objects)):
        dt = objects[i]["t"] - objects[i - 1]["t"]
        offset, beat_len = _beat_at(objects[i - 1]["t"], uninherited)
        if beat_len <= 0:
            continue
        sub = _classify_subdivision(dt, beat_len)
        if sub:
            subdivs.append(sub)

        # How far is this note from the 1/4 grid?
        rel_t = (objects[i]["t"] - offset) % beat_len
        quarter = beat_len / 4
        snap_err = min(rel_t % quarter, quarter - (rel_t % quarter))
        off_beat_distances.append(snap_err / quarter if quarter > 0 else 0.0)

    if not subdivs:
        return 0.0, 0.0, 0.0

    # ── Entropy ──
    counts: dict[str, int] = {}
    for s in subdivs:
        counts[s] = counts.get(s, 0) + 1
    total = len(subdivs)
    entropy = -sum(
        (c / total) * math.log(c / total)
        for c in counts.values() if c > 0
    )
    entropy_norm = min(entropy / math.log(8), 1.0)

    # ── Polyrhythm density: 4s sliding windows with 50% overlap ──
    poly_windows = total_windows = 0
    window_ms = 4000
    rare_subs = {"1/3", "1/6", "1/8", "1/16", "other"}
    if objects:
        t0 = objects[0]["t"]
        t_end = objects[-1]["t"]
        cursor = t0
        while cursor <= t_end:
            window_subs: set[str] = set()
            for i in range(1, len(objects)):
                ot = objects[i]["t"]
                if ot < cursor:
                    continue
                if ot >= cursor + window_ms:
                    break
                dt = ot - objects[i - 1]["t"]
                _, bl = _beat_at(objects[i - 1]["t"], uninherited)
                if bl <= 0:
                    continue
                s = _classify_subdivision(dt, bl)
                if s in rare_subs:
                    window_subs.add(s)
            if len(window_subs) >= 2:
                poly_windows += 1
            total_windows += 1
            cursor += window_ms // 2

    poly_density = poly_windows / total_windows if total_windows else 0.0

    # ── Off-beat ratio (mean) ──
    off_beat = sum(off_beat_distances) / len(off_beat_distances) if off_beat_distances else 0.0
    # Normalize: snap_err/quarter is in [0..0.5]; multiply by 2 → [0..1]
    off_beat = min(off_beat * 2.0, 1.0)

    return entropy_norm, poly_density, off_beat


def _jack_density(
    objects: list[dict],
    distance_threshold: float = 8.0,
    interval_min: float = 80.0,
    interval_max: float = 250.0,
) -> float:
    """Fraction of intervals where the next note is in nearly the same spot
    (jack = no movement, all timing).  Bounded interval rules out double-clicks."""
    if len(objects) < 2:
        return 0.0
    jack_count = 0
    n = len(objects)
    for i in range(1, n):
        d = _dist(objects[i - 1], objects[i])
        dt = objects[i]["t"] - objects[i - 1]["t"]
        if d < distance_threshold and interval_min <= dt <= interval_max:
            jack_count += 1
    return jack_count / max(n - 1, 1)


def _slider_tail_demand(objects: list[dict]) -> float:
    """Crude proxy for slider-tail accuracy demand.

    We don't compute real slider duration (needs SV from timing points), so
    we use slider length × repeats × 'is long' factor as a heuristic.  Maps
    with many long+repeating sliders score higher.
    """
    if not objects:
        return 0.0
    n = len(objects)
    slider_count = 0
    score = 0.0
    for obj in objects:
        if not obj.get("slider"):
            continue
        slider_count += 1
        length  = obj.get("length", 0.0)
        repeats = obj.get("repeats", 1)
        # 200 osu-px ≈ one full quarter-beat at SV 1.0; cap at 4× that
        len_factor    = min(length / 200.0, 4.0)
        repeat_factor = math.log1p(repeats - 1)         # repeats=1 → 0, =2 → 0.69, =4 → 1.39
        score += len_factor * (1.0 + repeat_factor)
    if not slider_count:
        return 0.0
    # Normalize by note count, soft-cap to [0..1]
    raw = score / n
    return raw / (raw + 1.0)


def _flow_break_density(
    objects: list[dict],
    angle_threshold: float = 2.36,    # ≈135°
    distance_min: float = 100.0,
) -> float:
    """Fraction of triplets that contain a flow-break: a sharp angle (>135°)
    AND both adjoining intervals are spaced (>100 px).  Pure aim signal."""
    n = len(objects)
    if n < 3:
        return 0.0
    breaks = 0
    triplets = 0
    for i in range(1, n - 1):
        dx1 = objects[i]["x"] - objects[i - 1]["x"]
        dy1 = objects[i]["y"] - objects[i - 1]["y"]
        dx2 = objects[i + 1]["x"] - objects[i]["x"]
        dy2 = objects[i + 1]["y"] - objects[i]["y"]
        len1 = math.sqrt(dx1 * dx1 + dy1 * dy1)
        len2 = math.sqrt(dx2 * dx2 + dy2 * dy2)
        if len1 < distance_min or len2 < distance_min:
            continue
        triplets += 1
        cos_a = (dx1 * dx2 + dy1 * dy2) / (len1 * len2)
        cos_a = max(-1.0, min(1.0, cos_a))
        angle = math.acos(cos_a)
        if angle > angle_threshold:
            breaks += 1
    return breaks / triplets if triplets else 0.0


def _bpm_relative_speed(
    intervals: list[float],
    beat_lengths: list[float],
) -> float:
    """Weighted speed signal: how fast notes are relative to the beat.

    Notes at 1/4-beat (sixteenths) score 1.0, 1/3 scores 0.8, 1/2 scores 0.4,
    slower notes score 0.  This gives a continuous signal rather than a harsh
    binary cutoff that collapses to near-zero for most maps."""
    if not intervals:
        return 0.0
    score = 0.0
    counted = 0
    for dt, bl in zip(intervals, beat_lengths):
        if bl <= 0 or dt <= 20.0:
            continue
        counted += 1
        ratio = bl / dt   # notes per beat
        if ratio >= 3.5:          # ~1/4 beat or faster
            score += 1.0
        elif ratio >= 2.5:        # ~1/3 beat
            score += 0.8
        elif ratio >= 1.8:        # ~1/2 beat
            score += 0.4
        elif ratio >= 1.3:        # borderline 1/2
            score += 0.15
    return score / counted if counted else 0.0


def _intensity_floor(
    objects: list[dict],
    window_s: int = 8,
) -> float:
    """Min density (notes/s) over sliding `window_s`-s windows, normalized so a
    fully-uniform map = 1.0 and a map with empty stretches = low.

    Returns ratio min_density / max_density (0..1)."""
    n = len(objects)
    if n < 2:
        return 0.0
    duration_s = (objects[-1]["t"] - objects[0]["t"]) / 1000.0
    if duration_s < window_s:
        return 1.0

    densities: list[float] = []
    window_ms = window_s * 1000
    step      = window_ms // 2
    sorted_t  = [o["t"] for o in objects]
    t_start   = sorted_t[0]
    t_end     = sorted_t[-1]

    lo = hi = 0
    cursor = t_start
    while cursor + window_ms <= t_end + step:
        w_end = cursor + window_ms
        while lo < n and sorted_t[lo] < cursor:
            lo += 1
        while hi < n and sorted_t[hi] < w_end:
            hi += 1
        count = hi - lo
        densities.append(count / window_s)
        cursor += step

    if not densities:
        return 0.0
    mn = min(densities)
    mx = max(densities)
    return mn / mx if mx > 0 else 0.0


def _pattern_repetition(objects: list[dict], block_size: int = 8) -> float:
    """Heuristic self-similarity: fraction of 8-note blocks that match
    another block (same relative XY/timing signature, coarsely binned).
    Higher = more repetitive (less consistency demand)."""
    n = len(objects)
    if n < block_size * 2:
        return 0.0
    sigs: list[tuple] = []
    for i in range(0, n - block_size, block_size):
        block = objects[i:i + block_size]
        x0, y0, t0 = block[0]["x"], block[0]["y"], block[0]["t"]
        sig = tuple(
            (
                round((b["x"] - x0) / 30.0) * 30,
                round((b["y"] - y0) / 30.0) * 30,
                round((b["t"] - t0) / 50.0) * 50,
            )
            for b in block
        )
        sigs.append(sig)
    if not sigs:
        return 0.0
    counts: dict[tuple, int] = {}
    for s in sigs:
        counts[s] = counts.get(s, 0) + 1
    repeats = sum(c - 1 for c in counts.values() if c > 1)
    return min(repeats / len(sigs), 1.0)


# ─── Empty / fallback ─────────────────────────────────────────────────────────

def _empty_features(n: int) -> dict:
    """Return a feature dict with all zeros (used for empty/short maps)."""
    return {
        # ── shared ──
        "note_count":            n,
        "duration_seconds":      0,
        "rhythm_complexity":     0.0,
        "stream_density":        0.0,
        # ── aim ──
        "jump_density":          0.0,
        "avg_jump_velocity":     0.0,
        "back_forth_ratio":      0.0,
        "angle_variance":        0.0,
        "flow_break_density":    0.0,
        # ── speed ──
        "burst_density":         0.0,
        "full_stream_density":   0.0,
        "death_stream_density":  0.0,
        "bpm_rel_speed":         0.0,
        # ── acc ──
        "subdiv_entropy":        0.0,
        "polyrhythm_density":    0.0,
        "off_beat_ratio":        0.0,
        "jack_density":          0.0,
        "slider_tail_demand":    0.0,
        "sv_variance":           0.0,
        "slider_density":        0.0,
        # ── cons ──
        "density_variance":      0.0,
        "intensity_floor":       0.0,
        "pattern_repetition":    0.0,
    }


# ─── Main feature extractor ───────────────────────────────────────────────────

def extract_features(osu_text: str) -> dict:
    """Parse a .osu file and return the full feature dict for the new
    AIM/SPEED/ACC/CONS pipeline.  Pure Python, no external deps.

    The dict carries every feature used by `compute_skill_intrinsics`, plus
    the few legacy-named features still referenced by older code (`stream_density`,
    `rhythm_complexity`, `density_variance`, etc.)."""
    objects       = _parse_hitobjects(osu_text)
    timing_points = _parse_timing_points(osu_text)

    if len(objects) < 2:
        return _empty_features(len(objects))

    n = len(objects)
    duration_ms = objects[-1]["t"] - objects[0]["t"]
    duration_s  = max(duration_ms / 1000.0, 1.0)

    uninherited = _build_beat_lookup(timing_points)

    intervals: list[float] = []
    distances: list[float] = []
    beat_lengths: list[float] = []
    for i in range(n - 1):
        dt = max(objects[i + 1]["t"] - objects[i]["t"], 1)
        intervals.append(float(dt))
        distances.append(_dist(objects[i], objects[i + 1]))
        _, bl = _beat_at(objects[i]["t"], uninherited)
        beat_lengths.append(bl)
    n_iv = len(intervals)

    # ── Stream / burst (SPEED) ──
    runs = _find_stream_runs(intervals, threshold=110)
    total_burst       = sum(r for r in runs if 2 <= r <= 4)
    total_stream      = sum(r for r in runs if 5 <= r <= 15)
    total_deathstream = sum(r for r in runs if r > 15)

    burst_density        = total_burst        / n
    full_stream_density  = total_stream       / n
    death_stream_density = total_deathstream  / n
    stream_density       = (total_burst + total_stream + total_deathstream) / n
    bpm_rel = _bpm_relative_speed(intervals, beat_lengths)

    # ── Jump (AIM) ──
    jump_velocities: list[float] = []
    jump_count = 0
    for dt, d in zip(intervals, distances):
        if dt >= 110 and d > 150:
            jump_count += 1
            jump_velocities.append(d / dt)
    jump_density      = jump_count / n_iv if n_iv else 0.0
    avg_jump_velocity = sum(jump_velocities) / len(jump_velocities) if jump_velocities else 0.0
    avg_jump_velocity = min(avg_jump_velocity / 3.0, 1.0)

    # ── Angle / flow-break (AIM) ──
    angles: list[float] = []
    for i in range(1, n - 1):
        dx1 = objects[i]["x"]     - objects[i - 1]["x"]
        dy1 = objects[i]["y"]     - objects[i - 1]["y"]
        dx2 = objects[i + 1]["x"] - objects[i]["x"]
        dy2 = objects[i + 1]["y"] - objects[i]["y"]
        l1 = math.sqrt(dx1 * dx1 + dy1 * dy1)
        l2 = math.sqrt(dx2 * dx2 + dy2 * dy2)
        if l1 > 5 and l2 > 5:
            cos_a = (dx1 * dx2 + dy1 * dy2) / (l1 * l2)
            cos_a = max(-1.0, min(1.0, cos_a))
            angles.append(math.acos(cos_a))
    back_forth_ratio = 0.0
    angle_variance   = 0.0
    if angles:
        back_forth_ratio = sum(1 for a in angles if a > 2.36) / len(angles)
        mean_a = sum(angles) / len(angles)
        std_a  = math.sqrt(sum((a - mean_a) ** 2 for a in angles) / len(angles))
        angle_variance = min(std_a / (math.pi / 2), 1.0)
    flow_break = _flow_break_density(objects)

    # ── Slider features (ACC / SPEED) ──
    slider_count   = sum(1 for o in objects if o.get("slider"))
    slider_density = slider_count / n
    sv_var         = _sv_variance(timing_points)
    slider_tail    = _slider_tail_demand(objects)

    # ── Subdivision / rhythm (ACC) ──
    subdiv_entropy, polyrhythm_density, off_beat = _subdivision_features(objects, uninherited)
    jack_dens = _jack_density(objects)

    # ── Rhythm complexity (general — kept for ML feature vector) ──
    if intervals:
        mean_dt = sum(intervals) / n_iv
        std_dt  = math.sqrt(sum((dt - mean_dt) ** 2 for dt in intervals) / n_iv)
        rhythm_complexity = min(std_dt / (mean_dt + 1e-9), 1.0)
    else:
        rhythm_complexity = 0.0

    # ── Density variance + floor + repetition (CONS) ──
    density_variance = 0.0
    if duration_s > 2:
        t0 = objects[0]["t"]
        n_windows = max(1, int(duration_s))
        counts = [0] * n_windows
        for obj in objects:
            w = min(int((obj["t"] - t0) / 1000), n_windows - 1)
            counts[w] += 1
        mean_c = sum(counts) / len(counts)
        std_c  = math.sqrt(sum((c - mean_c) ** 2 for c in counts) / len(counts))
        density_variance = min(std_c / (mean_c + 1e-9), 1.0)
    intensity_floor    = _intensity_floor(objects)
    pattern_repetition = _pattern_repetition(objects)

    return {
        # shared
        "note_count":            n,
        "duration_seconds":      int(duration_s),
        "rhythm_complexity":     round(rhythm_complexity, 4),
        "stream_density":        round(stream_density, 4),
        # aim
        "jump_density":          round(jump_density, 4),
        "avg_jump_velocity":     round(avg_jump_velocity, 4),
        "back_forth_ratio":      round(back_forth_ratio, 4),
        "angle_variance":        round(angle_variance, 4),
        "flow_break_density":    round(flow_break, 4),
        # speed
        "burst_density":         round(burst_density, 4),
        "full_stream_density":   round(full_stream_density, 4),
        "death_stream_density":  round(death_stream_density, 4),
        "bpm_rel_speed":         round(bpm_rel, 4),
        # acc
        "subdiv_entropy":        round(subdiv_entropy, 4),
        "polyrhythm_density":    round(polyrhythm_density, 4),
        "off_beat_ratio":        round(off_beat, 4),
        "jack_density":          round(jack_dens, 4),
        "slider_tail_demand":    round(slider_tail, 4),
        "sv_variance":           round(sv_var, 4),
        "slider_density":        round(slider_density, 4),
        # cons
        "density_variance":      round(density_variance, 4),
        "intensity_floor":       round(intensity_floor, 4),
        "pattern_repetition":    round(pattern_repetition, 4),
    }


# ─── Skill intrinsics + stars ────────────────────────────────────────────────

def compute_skill_intrinsics(
    features: dict,
    bpm:    float = 0.0,
    ar:     float = 0.0,
    od:     float = 0.0,
    length_s: int = 0,
) -> dict:
    """Per-skill intrinsic [0..1] from features + metadata.  Each axis is
    independent — no zero-sum normalization — so an ACC-pure map can score
    high on ACC without being penalised on AIM/SPEED."""

    def f(k: str, default: float = 0.0) -> float:
        v = features.get(k, default)
        return float(v) if v is not None else default

    nc  = f("note_count", 0)
    dur = max(f("duration_seconds", 1), 1)
    nps = nc / dur
    nps_n = min(nps / 8.0, 1.0)                     # 8 nps = saturated

    bpm_n  = min(bpm / 240.0, 1.0) if bpm > 0 else 0.4
    ar_n   = min(ar  / 11.0,  1.0) if ar  > 0 else 0.5
    od_eff = max(0.0, (od - 5.0) / 5.0) if od > 0 else 0.0   # OD 5..10 → 0..1

    # ── AIM — spatial precision ──
    aim = (
        0.30 * f("avg_jump_velocity") +
        0.20 * f("jump_density") +
        0.20 * f("flow_break_density") +
        0.15 * f("angle_variance") +
        0.10 * f("back_forth_ratio") +
        0.05 * ar_n
    )

    # ── SPEED — tempo density (BPM-relative is the core signal) ──
    speed = (
        0.25 * f("bpm_rel_speed") +
        0.25 * f("full_stream_density") +
        0.20 * f("death_stream_density") +
        0.15 * f("burst_density") +
        0.10 * nps_n +
        0.05 * bpm_n
    )

    # ── ACC — temporal precision ──
    od_demand = od_eff * (0.4 + 0.6 * nps_n)        # OD matters with density
    acc = (
        0.25 * f("subdiv_entropy") +
        0.20 * f("polyrhythm_density") +
        0.15 * f("jack_density") +
        0.15 * od_demand +
        0.10 * f("off_beat_ratio") +
        0.10 * f("slider_tail_demand") +
        0.05 * f("sv_variance")
    )

    # ── CONS — endurance / sustained intensity ──
    # Gate uniformity behind intensity_floor: low-variance means nothing
    # unless the map actually maintains high density throughout.
    # (1-pattern_repetition) dropped — it's ≈1.0 for all maps, no signal.
    len_n = min(length_s / 360.0, 1.0) if length_s > 0 else 0.0
    floor = f("intensity_floor")
    gated_uniformity = (1.0 - f("density_variance")) * min(floor * 2.0, 1.0)
    cons = (
        0.35 * floor +
        0.25 * len_n +
        0.25 * gated_uniformity +
        0.15 * nps_n
    )

    return {
        "aim":   max(0.0, min(1.0, aim)),
        "speed": max(0.0, min(1.0, speed)),
        "acc":   max(0.0, min(1.0, acc)),
        "cons":  max(0.0, min(1.0, cons)),
    }


def compute_skill_stars(
    features: dict,
    bpm: float = 0.0,
    ar:  float = 0.0,
    od:  float = 0.0,
    length_s: int = 0,
    star_rating: float = 0.0,
    *,
    api_aim:   float = 0.0,
    api_speed: float = 0.0,
) -> dict:
    """Independent skill stars in [0..10].

    Scaling: `star_rating` from osu! API anchors the absolute scale.  An
    aim-pure 5★ map → ~5★ aim, ~1-2★ on others.  AIM and SPEED additionally
    blend with osu! API attributes (api_aim_difficulty, api_speed_difficulty)
    when available — those are themselves absolute difficulties on the same
    scale, so the blend is well-defined."""
    intr = compute_skill_intrinsics(features, bpm=bpm, ar=ar, od=od, length_s=length_s)
    sr = max(star_rating, 0.5)

    # CONS multiplier scales with SR: at low SR (≤4) maps are short and
    # consistency doesn't matter much (1.1×); at high SR (≥7) long endurance
    # maps need a competitive CONS score to not lose to SPEED/AIM (1.5×).
    cons_mult = min(1.1 + max(0.0, sr - 4.0) * 0.133, 1.5)

    aim_stars   = intr["aim"]   * sr * 1.5
    speed_stars = intr["speed"] * sr * 1.8
    acc_stars   = intr["acc"]   * sr * 1.8
    cons_stars  = intr["cons"]  * sr * cons_mult

    # Blend with osu! API absolute difficulties when present (40% API)
    if api_aim > 0:
        aim_stars   = 0.6 * aim_stars   + 0.4 * api_aim
    if api_speed > 0:
        speed_stars = 0.6 * speed_stars + 0.4 * api_speed

    return {
        "aim":   round(min(aim_stars,   10.0), 2),
        "speed": round(min(speed_stars, 10.0), 2),
        "acc":   round(min(acc_stars,   10.0), 2),
        "cons":  round(min(cons_stars,  10.0), 2),
    }


def stars_to_weights(stars: dict, temperature: float = 2.0) -> dict:
    """Softmax over per-skill stars → share-weights summing to 1.

    Lower temperature ⇒ sharper dominant component.  T=2.0 is a reasonable
    middle ground: a 7★/4★/3★/3★ map gives roughly {0.45, 0.18, 0.18, 0.19}."""
    if not stars:
        return {"aim": 0.25, "speed": 0.25, "acc": 0.25, "cons": 0.25}
    max_s = max(stars.values())
    exp_vals = {k: math.exp((v - max_s) / max(temperature, 0.01)) for k, v in stars.items()}
    total = sum(exp_vals.values()) or 1.0
    return {k: round(v / total, 3) for k, v in exp_vals.items()}


def map_type_from_stars(stars: dict) -> str:
    """argmax over the four-axis star vector."""
    return max(stars, key=stars.get)


# ─── Legacy API (kept for callers that don't have SR yet) ────────────────────

def weights_from_features(
    features: dict,
    bpm: float = 0.0,
    ar:  float = 0.0,
    od:  float = 0.0,
    *,
    api_aim:           float = 0.0,
    api_speed:         float = 0.0,
    api_slider_factor: float = 1.0,   # accepted but no longer used
) -> dict:
    """LEGACY — share-weights for callers that don't have SR.

    With the new pipeline this falls back to softmax over **intrinsic** scores
    (no SR multiplier).  Prefer `compute_skill_stars` + `stars_to_weights`."""
    length_s = int(features.get("duration_seconds", 0) or 0)
    intr = compute_skill_intrinsics(features, bpm=bpm, ar=ar, od=od, length_s=length_s)
    # Treat intrinsics-as-stars and softmax them so that a discriminating
    # intrinsic profile turns into a peaked share.
    fake_stars = {k: v * 10.0 for k, v in intr.items()}
    if api_aim > 0:
        fake_stars["aim"]   = 0.6 * fake_stars["aim"]   + 0.4 * api_aim
    if api_speed > 0:
        fake_stars["speed"] = 0.6 * fake_stars["speed"] + 0.4 * api_speed
    return stars_to_weights(fake_stars, temperature=2.0)


def map_type_from_weights(weights: dict) -> str:
    return max(weights, key=weights.get)
