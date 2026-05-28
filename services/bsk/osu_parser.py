"""BSK-specific skill calibration on top of the shared `.osu` feature extractor.

PHASE 3 — split.  The pure parser layer (`_parse_hitobjects`,
`_parse_timing_points`, `extract_features`, ...) lives in
`utils/osu/parser_core.py` and is shared with the HPS pipeline. This
module keeps only what is BSK-specific:

  * compute_skill_intrinsics — weighted [0..1] per-skill scores
  * compute_skill_stars      — ML-calibrated [0..10] per-skill stars
                               (with 20% osu! API blend on aim/speed)
  * stars_to_weights         — softmax(stars / T) → share-weights
  * map_type_from_stars      — argmax with `mixed` fallback

Plus two legacy functions still imported by old call-sites:
  * weights_from_features    — share-weights without SR
  * map_type_from_weights    — argmax on share-weights

History: split out of a single 850-line module on 2026-05-28 per plan
`unified-giggling-tiger`. All parser internals are re-exported below
so existing call-sites (`replay_parser.py`, `bsk_pool.py`, etc.) keep
working without code changes. New code should import the parser core
directly from `utils.osu.parser_core`.
"""

import math

# Re-export the pure parser layer so existing imports
# `from services.bsk.osu_parser import _parse_hitobjects, extract_features, ...`
# keep working. New code should import from `utils.osu.parser_core` directly.
from utils.osu.parser_core import (  # noqa: F401
    extract_features,
    _parse_hitobjects,
    _parse_timing_points,
    _dist,
    _build_beat_lookup,
    _beat_at,
    _classify_subdivision,
    _find_stream_runs,
    _sv_variance,
    _subdivision_features,
    _jack_density,
    _slider_tail_demand,
    _flow_break_density,
    _bpm_relative_speed,
    _intensity_floor,
    _pattern_repetition,
    _empty_features,
)


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
    # Log-curve length scaling: TV-size maps (≤120s) are capped low, marathons
    # saturate around 600s.  nps_n is back in the base — it's not redundant with
    # SPEED here because cons measures *sustained* density, not peak bursts.
    floor = f("intensity_floor")
    gated_uniformity = (1.0 - f("density_variance")) * min(floor * 2.0, 1.0)
    cons_base = (
        0.35 * floor +
        0.35 * gated_uniformity +
        0.30 * nps_n
    )
    # len_factor: log curve clamped [0.35, 1.0], saturates at 360s (6 min)
    t = max(0, length_s)
    len_factor = 0.35 + 0.65 * math.log(1.0 + t / 180.0) / math.log(1.0 + 360.0 / 180.0)
    len_factor = max(0.35, min(1.0, len_factor))
    cons = cons_base * len_factor

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

    aim_stars   = intr["aim"]   * sr * 1.5
    speed_stars = intr["speed"] * sr * 1.8
    acc_stars   = intr["acc"]   * sr * 1.8
    # Ramp cons_mult from 1.2 (SR≤2) to 2.4 (SR≥8)
    cons_mult   = min(1.2 + 0.2 * max(0.0, sr - 2.0), 2.4)
    cons_stars  = intr["cons"]  * sr * cons_mult

    # Blend with osu! API absolute difficulties when present (20% API).
    # Pool audit (May 2026) showed the previous 40% blend dominated intrinsics
    # (Pearson r ≈ 0.97 between aim_stars and api_aim_diff), flattening the
    # distinction between aim and stream maps of similar SR. Reducing to 20%
    # keeps the API as a sanity anchor without drowning the parser features.
    if api_aim > 0:
        aim_stars   = 0.8 * aim_stars   + 0.2 * api_aim
    if api_speed > 0:
        speed_stars = 0.8 * speed_stars + 0.2 * api_speed

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


def map_type_from_stars(stars: dict, margin_threshold: float = 0.3) -> str:
    """argmax over the four-axis star vector, with a 'mixed' fallback.

    A pool audit (May 2026) showed that 50% of maps have a top1−top2 margin
    below 0.3★ — pure argmax was effectively random for those maps.

    When `margin_threshold > 0` and the gap between the top two axes is below
    that threshold, we return `'mixed'` instead of the noisy winner. Downstream
    consumers (map_selector, image renderers) already handle 'mixed' as a
    distinct, non-component-locked type.

    Set `margin_threshold=0` to recover the old argmax-only behaviour (e.g.
    for unit tests of intrinsic balance, where the margin is incidental).
    """
    if not stars:
        return "mixed"
    sorted_axes = sorted(stars.items(), key=lambda kv: kv[1], reverse=True)
    top_axis, top_value = sorted_axes[0]
    if margin_threshold > 0 and len(sorted_axes) >= 2:
        runner_up_value = sorted_axes[1][1]
        if (top_value - runner_up_value) < margin_threshold:
            return "mixed"
    return top_axis


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


def map_type_from_weights(weights: dict, margin_threshold: float = 0.05) -> str:
    """argmax over share-weights with a 'mixed' fallback.

    Mirrors `map_type_from_stars`, but operates in the [0..1] weights space.
    Default `margin_threshold=0.05` is the rough weights-equivalent of the
    0.5★ threshold under softmax temperature=2 (a 0.5★ gap in stars produces
    ~0.05 difference in shares). Pass 0 to recover plain argmax.
    """
    if not weights:
        return "mixed"
    sorted_axes = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)
    top_axis, top_value = sorted_axes[0]
    if margin_threshold > 0 and len(sorted_axes) >= 2:
        runner_up_value = sorted_axes[1][1]
        if (top_value - runner_up_value) < margin_threshold:
            return "mixed"
    return top_axis
