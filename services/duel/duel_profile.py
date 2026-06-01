"""DUEL profile: ML-calibrated per-axis skill stars from a .osu file.

Plan: unified-giggling-tiger (DUEL ⇄ HPS split).

This module wraps the DUEL-specific calibration that turns the shared
24 raw features (from `utils/osu/parser_core.py`) into:

  * per-axis stars [0..10]   — absolute skill demand on each axis
  * share-weights            — softmax(stars/T) for the HPS payout formula
  * map_type tag             — argmax with mixed-fallback for UI / dueling

The function `compute_duel_profile` is the **only** public entry point.
`services.duel.map_pool.analyze_map` is now a thin shim that delegates
here — keeping that legacy name working for `/duelrecalc` and friends.

Why this file exists separately:

  1. HPS no longer goes through this calibration. It builds its own
     profile (genre tags, length bucket, bpm bucket) in
     `services/hps/hps_profile.py`. Both share the parser core but
     diverge at the calibration step.

  2. The DUEL calibration is tuned against the DUEL duel pool and the
     ML inference targets — any future re-tuning of these multipliers
     stays scoped to this file and does not affect HPS payouts.

  3. Tests can mock `compute_duel_profile` independently of the parser.
"""

from __future__ import annotations

from typing import Optional

from utils.osu.parser_core import extract_features


def compute_duel_profile(
    osu_text: Optional[str],
    *,
    bpm: float,
    ar: float,
    od: float,
    length_s: int,
    star_rating: float,
    api_aim: float = 0.0,
    api_speed: float = 0.0,
) -> dict:
    """One-stop DUEL pipeline: parse .osu → features → stars → weights → map_type.

    `osu_text` may be None when only metadata is available (e.g. /duelrecalc
    runs without re-downloading); in that case the parser returns an empty
    feature dict and intrinsics fall back to BPM/AR/OD/length signals.

    Returns:
        {
          'features':  dict — full parsed feature dict (or empties),
          'intrinsic': dict — per-skill [0..1],
          'stars':     dict — per-skill [0..10] (aim/speed/acc/cons),
          'weights':   dict — softmax share-weights summing to 1.0,
          'map_type':  str  — argmax over stars,
        }
    """
    # Lazy imports to avoid an import-cycle: duel_profile is imported by
    # `services.duel.map_pool`, which in turn re-exports this calibration via
    # the legacy `analyze_map` name.
    from services.duel.osu_parser import (
        compute_skill_intrinsics,
        compute_skill_stars,
        stars_to_weights,
        classify_map_type,
    )

    if osu_text:
        features = extract_features(osu_text)
    else:
        # No .osu — feed an empty dict; intrinsics will be metadata-driven only.
        features = {
            "note_count": 0, "duration_seconds": length_s or 0,
        }

    intrinsic = compute_skill_intrinsics(
        features, bpm=bpm, ar=ar, od=od, length_s=length_s,
    )
    stars = compute_skill_stars(
        features, bpm=bpm, ar=ar, od=od, length_s=length_s,
        star_rating=star_rating, api_aim=api_aim, api_speed=api_speed,
    )
    weights  = stars_to_weights(stars)
    # Two-gate classifier (2026-05-31): Gate-1 disqualifies axes without a
    # characteristic feature signal; Gate-2 argmax with per-axis margins.
    # `confidence` is "specialist" | "leaning" | "mixed" — used by /dueldiag
    # for pool calibration, NOT shown on duel cards (which display only the
    # `map_type` string).
    map_type, confidence = classify_map_type(stars, features, length_s)
    return {
        "features":   features,
        "intrinsic":  intrinsic,
        "stars":      stars,
        "weights":    weights,
        "map_type":   map_type,
        "confidence": confidence,
    }


__all__ = ["compute_duel_profile"]
