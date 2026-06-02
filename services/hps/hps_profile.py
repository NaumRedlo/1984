"""HPS profile: genre / length / BPM buckets and per-bounty-type suitability.

Plan: unified-giggling-tiger (DUEL ⇄ HPS split, step 3/9).

This module profiles maps for the HPS bounty pool. It consumes the shared
`utils/osu/parser_core.py:extract_features`:

  * HPS produces human-readable buckets (genre / length / BPM) and a
    `typing_hints` dict that scores each bounty_type's suitability for
    the map, used by the weekly generator to pick variety.

The design intent: HPS pool selection should not depend on the DUEL ML
calibration. A new ranked map can be ingested into `hps_map_pool` and
used by weekly bounties without ever going through the DUEL pipeline —
and vice-versa.

Rule-based by design. No ML, no API blending. The `typing_hints` are
simple linear / threshold rules over parser features. Each rule has a
short rationale in the code so the next person re-tuning them
understands what the score is trying to express.

Public API:
    compute_hps_profile(osu_text, *, bpm, ar, od, length_s,
                        star_rating, ranked_status) -> dict

Output schema:
    {
      'features':       dict — parsed feature dict (or empty for None input),
      'genre_tag':      str  — "stream" | "jump" | "tech" | "mixed",
      'length_bucket':  str  — "short" | "medium" | "long" | "marathon",
      'bpm_bucket':     str  — "slow"  | "mid"    | "fast" | "speedcore",
      'ranked_status':  str  — passed through (caller knows the API answer),
      'typing_hints':   dict — {bounty_type: 0..1 suitability} for the 7 types.
    }
"""

from __future__ import annotations

import math
from typing import Optional

from utils.osu.parser_core import extract_features


# ── Bucket boundaries ──────────────────────────────────────────────────────

# Length buckets (drain time, seconds). Marathon threshold mirrors the
# `_is_marathon` cutoff in services/bounty/tier_rules.py so they stay in sync.
LENGTH_SHORT_MAX    = 120     # ≤ 2 min   — TV-size
LENGTH_MEDIUM_MAX   = 300     # ≤ 5 min   — standard
LENGTH_LONG_MAX     = 600     # ≤ 10 min  — long
# > 600 → "marathon"

# BPM buckets — coarse, reflects typical osu! ranked distribution.
BPM_SLOW_MAX        = 150
BPM_MID_MAX         = 200
BPM_FAST_MAX        = 250
# > 250 → "speedcore"


def _length_bucket(length_s: int) -> str:
    if length_s <= LENGTH_SHORT_MAX:
        return "short"
    if length_s <= LENGTH_MEDIUM_MAX:
        return "medium"
    if length_s <= LENGTH_LONG_MAX:
        return "long"
    return "marathon"


def _bpm_bucket(bpm: float) -> str:
    if bpm <= 0:
        return "mid"  # unknown → assume mid
    if bpm < BPM_SLOW_MAX:
        return "slow"
    if bpm < BPM_MID_MAX:
        return "mid"
    if bpm < BPM_FAST_MAX:
        return "fast"
    return "speedcore"


# ── Genre tag ───────────────────────────────────────────────────────────────

def _genre_tag(features: dict) -> str:
    """Coarse genre classifier from feature dominance.

    Three buckets + 'mixed' fallback when no dominant signal:
      stream — strong full/death-stream density
      jump   — high jump density × velocity
      tech   — high subdivision entropy or polyrhythm

    The thresholds are deliberately loose. The goal is informational
    (UI / weekly-pool variety enforcement), not strict classification.
    """
    stream_signal = features.get("full_stream_density", 0.0) + \
                    features.get("death_stream_density", 0.0)
    jump_signal   = features.get("jump_density", 0.0) * (
                    1.0 + features.get("avg_jump_velocity", 0.0))
    tech_signal   = features.get("subdiv_entropy", 0.0) + \
                    features.get("polyrhythm_density", 0.0)

    scores = {"stream": stream_signal, "jump": jump_signal, "tech": tech_signal}
    top = max(scores, key=scores.get)
    top_val = scores[top]
    # Margin guard: if the top score is feeble (< 0.15) OR within 0.05 of the
    # runner-up, classify as "mixed". 0.15 is the floor below which the parser
    # features are essentially noise (empty maps, short TV-size cuts).
    if top_val < 0.15:
        return "mixed"
    rest = [v for k, v in scores.items() if k != top]
    if rest and (top_val - max(rest)) < 0.05:
        return "mixed"
    return top


# ── Typing hints — per-bounty-type suitability score ────────────────────────
#
# Each function returns a [0..1] suitability score. The weekly generator can
# use these to pick maps that suit a given bounty type *better than random*.
# All rules are pure functions of (features, metadata).

def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def _hint_marathon(features: dict, length_s: int, **_) -> float:
    """1.0 above the 10-min marathon cutoff, ramping up from 0 at 5 min."""
    if length_s >= 600:
        return 1.0
    if length_s < 300:
        return 0.0
    # Linear ramp 300..600 → 0..1
    return _clip((length_s - 300) / 300.0)


def _hint_ss(features: dict, od: float, length_s: int, **_) -> float:
    """SS suitability: short maps with high OD + clean acc features.

    SS requires 100% accuracy. We want short(er) maps with low miss-bait
    aim noise (low jump_density, low flow_break) and steady patterns.
    """
    if length_s > 360:
        return 0.0  # Too long for SS — fatigue rules out perfect-acc runs.
    od_factor = _clip((od - 5.0) / 5.0)  # OD 5..10 → 0..1
    aim_chaos = features.get("flow_break_density", 0.0) + \
                features.get("angle_variance", 0.0) * 0.5
    cleanliness = _clip(1.0 - aim_chaos)
    return _clip(0.4 * od_factor + 0.6 * cleanliness)


def _hint_accuracy(features: dict, od: float, **_) -> float:
    """Accuracy bounty: OD-demanding maps with tight subdivisions.

    Less strict than SS — we want clearly acc-loaded maps but allow some
    aim/jump content too. High subdiv_entropy + polyrhythm + decent OD.
    """
    od_factor = _clip((od - 4.0) / 6.0)
    subdiv    = features.get("subdiv_entropy", 0.0)
    poly      = features.get("polyrhythm_density", 0.0)
    jacks     = features.get("jack_density", 0.0)
    return _clip(0.35 * od_factor + 0.30 * subdiv + 0.20 * poly + 0.15 * jacks)


def _hint_metronome(features: dict, **_) -> float:
    """Metronome (UR-bounded): steady-rhythm maps with low off-beat noise.

    Want: low off_beat_ratio, low polyrhythm (consistent grid), high
    intensity_floor (no rest sections to game the UR window).
    """
    off_beat = features.get("off_beat_ratio", 0.0)
    poly     = features.get("polyrhythm_density", 0.0)
    floor    = features.get("intensity_floor", 0.0)
    steadiness = _clip(1.0 - off_beat - poly * 0.5)
    return _clip(0.6 * steadiness + 0.4 * floor)


def _hint_mod(features: dict, star_rating: float, **_) -> float:
    """Mod (HD/HR/DT) bounty: low/mid SR maps with simple patterns.

    Mod-rotation bounties are warm-ups — we want clearly accessible maps
    that gain meaningful difficulty from the mod. SR < 5 is the sweet spot.
    """
    if star_rating <= 0:
        return 0.5  # Unknown → neutral
    if star_rating < 4.5:
        sr_factor = 1.0
    elif star_rating < 6.0:
        sr_factor = 1.0 - (star_rating - 4.5) / 1.5  # linear fade 4.5..6.0 → 1..0
    else:
        sr_factor = 0.0
    # Penalise complexity — mod-warmups should not also be tech-heavy.
    complexity = features.get("subdiv_entropy", 0.0) + \
                 features.get("polyrhythm_density", 0.0)
    simple = _clip(1.0 - complexity * 0.5)
    return _clip(0.7 * sr_factor + 0.3 * simple)


def _hint_pass(features: dict, star_rating: float, length_s: int, **_) -> float:
    """Pass bounty: hard but completeable. High SR, decent length, no marathon.

    Pass = just survive the map. Reward high SR + medium-long length;
    marathons are their own bounty type so down-weight them here.
    """
    if star_rating <= 0:
        return 0.0
    sr_factor = _clip((star_rating - 5.0) / 4.0)  # SR 5..9 → 0..1
    # Prefer 3-9 min (180-540s); short maps are too easy to call "pass".
    if length_s < 120:
        len_factor = 0.0
    elif length_s < 540:
        len_factor = _clip((length_s - 120) / 60.0)  # 120..180 → 0..1
    elif length_s < 600:
        len_factor = 1.0
    else:
        len_factor = 0.3  # Marathon range — covered by _hint_marathon instead.
    return _clip(0.7 * sr_factor + 0.3 * len_factor)


def _hint_first_fc(features: dict, star_rating: float, length_s: int, **_) -> float:
    """First FC: any map can be one. Returns mid suitability everywhere.

    First FC is the fallback bounty type — neutral 0.5 across the pool so
    it gets picked when no other type stands out. Slightly higher for
    medium-length maps (3-6 min) which are the "comfortable" FC range.
    """
    if 180 <= length_s <= 360:
        return 0.6
    return 0.5


# Map bounty_type names (as used by tier_rules.assign_bounty_type) to their
# suitability functions. Order is informational; the weekly generator does
# not depend on dict insertion order.
_TYPE_HINTS = {
    "Marathon":  _hint_marathon,
    "SS":        _hint_ss,
    "Accuracy":  _hint_accuracy,
    "Metronome": _hint_metronome,
    "Mod":       _hint_mod,
    "Pass":      _hint_pass,
    "First FC":  _hint_first_fc,
}


# ── Public API ──────────────────────────────────────────────────────────────

def compute_hps_profile(
    osu_text: Optional[str],
    *,
    bpm: float,
    ar: float,
    od: float,
    length_s: int,
    star_rating: float,
    ranked_status: str = "ranked",
) -> dict:
    """Compute the HPS-side profile for a beatmap.

    `osu_text` may be None — in that case `features` is the empty-features
    dict (24 keys, all zero except note_count and duration_seconds). The
    typing_hints are still computed from metadata signals where possible
    (Marathon and Pass don't strictly need features; Mod uses SR only).
    """
    if osu_text:
        features = extract_features(osu_text)
    else:
        features = {
            "note_count": 0, "duration_seconds": length_s or 0,
            "rhythm_complexity": 0.0, "stream_density": 0.0,
            "jump_density": 0.0, "avg_jump_velocity": 0.0,
            "back_forth_ratio": 0.0, "angle_variance": 0.0,
            "flow_break_density": 0.0,
            "burst_density": 0.0, "full_stream_density": 0.0,
            "death_stream_density": 0.0, "bpm_rel_speed": 0.0,
            "subdiv_entropy": 0.0, "polyrhythm_density": 0.0,
            "off_beat_ratio": 0.0, "jack_density": 0.0,
            "slider_tail_demand": 0.0, "sv_variance": 0.0,
            "slider_density": 0.0,
            "density_variance": 0.0, "intensity_floor": 0.0,
            "pattern_repetition": 0.0,
        }

    typing_hints = {
        bt: round(fn(
            features,
            bpm=bpm, ar=ar, od=od,
            length_s=length_s, star_rating=star_rating,
        ), 3)
        for bt, fn in _TYPE_HINTS.items()
    }

    return {
        "features":      features,
        "genre_tag":     _genre_tag(features),
        "length_bucket": _length_bucket(length_s),
        "bpm_bucket":    _bpm_bucket(bpm),
        "ranked_status": ranked_status,
        "typing_hints":  typing_hints,
    }


__all__ = ["compute_hps_profile"]
