"""
Composite score for BSK duel round comparison.
0.7·(accuracy * combo_ratio) + 0.3·miss_penalty
Pure execution metric — no pp dependency.
"""

POINTS_MULTIPLIER = 500_000           # ranked / default
POINTS_MULTIPLIER_CASUAL = 250_000    # casual: smaller scale → multipliers actually matter
FAILED_POINTS_MULTIPLIER = 0.75


def points_multiplier_for(mode: str) -> int:
    return POINTS_MULTIPLIER_CASUAL if mode == 'casual' else POINTS_MULTIPLIER


def composite_score(
    accuracy: float, # 0.0 – 100.0
    combo: int,
    max_combo: int,
    misses: int,
) -> float:
    """Returns a normalized composite score in [0, 1]."""
    acc_norm = accuracy / 100.0
    combo_ratio = (combo / max_combo) if max_combo > 0 else 0.0
    miss_penalty = 1.0 / (1.0 + misses / 3)

    return (
        0.7 * acc_norm * combo_ratio +
        0.3 * miss_penalty
    )


def composite_points(
    accuracy: float,
    combo: int,
    max_combo: int,
    misses: int,
    passed: bool = True,
    mode: str = 'ranked',
) -> int:
    """Composite score scaled to integer race points.

    A submitted score always advances the score-race, but failed submits are
    still penalized. Missing score entries are handled by forfeit logic and
    should not call this function.
    """
    raw_points = composite_score(accuracy, combo, max_combo, misses) * points_multiplier_for(mode)
    if not passed:
        raw_points *= FAILED_POINTS_MULTIPLIER
    return int(raw_points)


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
