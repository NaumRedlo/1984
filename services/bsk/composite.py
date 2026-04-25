"""
Composite score for BSK duel round comparison.
0.7·(accuracy * combo_ratio) + 0.3·miss_penalty
Pure execution metric — no pp dependency.
"""

import math

POINTS_MULTIPLIER = 200_000


def composite_score(
    pp: float,       # unused, kept for API compatibility
    accuracy: float, # 0.0 – 100.0
    combo: int,
    max_combo: int,
    misses: int,
) -> float:
    """Returns a normalized composite score in [0, 1]."""
    acc_norm = accuracy / 100.0
    combo_ratio = (combo / max_combo) if max_combo > 0 else 0.0
    miss_penalty = 1.0 / (1.0 + misses / 5.0)

    return (
        0.7 * acc_norm * combo_ratio +
        0.3 * miss_penalty
    )


def composite_points(
    pp: float,
    accuracy: float,
    combo: int,
    max_combo: int,
    misses: int,
) -> int:
    """Composite score scaled to integer points (max: 200,000 per round)."""
    return int(composite_score(pp, accuracy, combo, max_combo, misses) * POINTS_MULTIPLIER)


def map_weights_from_features(
    stream_density: float = 0.0,
    jump_density: float = 0.0,
    slider_density: float = 0.0,
    rhythm_complexity: float = 0.0,
) -> dict:
    """
    Estimate map skill weights from basic map features.
    Returns dict with keys: aim, speed, acc, cons — summing to 1.0.
    Used before ML model is available.
    """
    # speed driven by streams, aim by jumps, acc by OD/sliders, cons by rhythm
    raw = {
        'aim':   jump_density,
        'speed': stream_density,
        'acc':   slider_density,
        'cons':  rhythm_complexity,
    }
    total = sum(raw.values()) or 1.0
    return {k: v / total for k, v in raw.items()}


DEFAULT_WEIGHTS = {'aim': 0.25, 'speed': 0.25, 'acc': 0.25, 'cons': 0.25}
