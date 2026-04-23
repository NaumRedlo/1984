"""
Composite score for BSK duel comparison.
Neutral to stable/lazer: 0.4·pp + 0.3·accuracy + 0.2·combo_ratio + 0.1·miss_penalty
"""


def composite_score(
    pp: float,
    accuracy: float,       # 0.0 – 100.0
    combo: int,
    max_combo: int,
    misses: int,
) -> float:
    """
    Returns a normalized composite score in range [0, 1].

    pp            — raw pp value (normalized against 1000 as reference ceiling)
    accuracy      — percentage 0–100
    combo_ratio   — combo / max_combo
    miss_penalty  — 1 - min(misses / 10, 1)
    """
    pp_norm = min(pp / 1000.0, 1.0)
    acc_norm = accuracy / 100.0
    combo_ratio = (combo / max_combo) if max_combo > 0 else 0.0
    miss_penalty = 1.0 - min(misses / 10.0, 1.0)

    return (
        0.4 * pp_norm +
        0.3 * acc_norm +
        0.2 * combo_ratio +
        0.1 * miss_penalty
    )
