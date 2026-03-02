# utils/hp_calculator.py
"""
Hunter Progression System (HPS) Calculator
Version 2.0 - Dynamic DM, Log-LSS, Relativity Factor
"""

import math
from typing import Optional


def calculate_dynamic_dm(star_rating: float) -> float:
    """
    Calculates Dynamic Difficulty Multiplier based on star rating.
    Linear scale from 4★ (x0.8) to 9★ (x2.0).
    
    Formula: DM = max(0.8, min(2.0, (StarRating × 0.24) - 0.16))
    """
    dm = (star_rating * 0.24) - 0.16
    return max(0.8, min(2.0, dm))


def calculate_log_lss(drain_time_seconds: int) -> float:
    """
    Calculates Logarithmic Length Scaling System based on drain time.
    Prevents inflation on marathons and supports short maps.
    
    Formula: LSS = 0.7 + (0.3 × log₂(DrainTimeSeconds / 60 + 1))
    """
    if drain_time_seconds <= 0:
        return 0.7
    
    lss = 0.7 + (0.3 * math.log2(drain_time_seconds / 60 + 1))
    return max(0.7, min(2.0, lss))


def calculate_relativity_factor(player_pp: int, expected_pp: int) -> float:
    """
    Calculates Relativity Factor based on player's PP vs expected PP for the map.
    Rewards progress and punishes farming easy maps.
    
    Formula: RF = 1.0 + ((ExpectedPP - PlayerPP) / 5000)
    Limits: 0.80 (min) to 1.50 (max)
    """
    rf = 1.0 + ((expected_pp - player_pp) / 5000)
    return max(0.80, min(1.50, rf))


def calculate_base_hp(result_type: str) -> int:
    """
    Returns Base HP based on result type.
    """
    base_hp_table = {
        "win": 100,           # Победа
        "condition": 60,      # Условие выполнено
        "partial": 30,        # Частично выполнено
        "participation": 10,  # Участие
        "sponsor": 20,        # Спонсорство
    }
    return base_hp_table.get(result_type.lower(), 0)


def calculate_bonuses(
    accuracy: float,
    is_full_combo: bool,
    is_first_submission: bool,
    has_zero_fifty: bool,
    extra_challenge: bool,
) -> int:
    """
    Calculates total bonus HP based on performance.
    Cap: +50 HP maximum.
    """
    bonuses = 0
    
    # Flawless Execution (100%)
    if accuracy >= 100.0:
        bonuses += 25
    # Elite Precision (≥99%)
    elif accuracy >= 99.0:
        bonuses += 15
    
    # Vanguard (first submission)
    if is_first_submission:
        bonuses += 15
    
    # Zero Fifty
    if has_zero_fifty:
        bonuses += 10
    
    # Extra Challenge
    if extra_challenge:
        bonuses += 20
    
    # Cap bonuses at +50 HP
    return min(bonuses, 50)


def calculate_final_hp(
    result_type: str,
    star_rating: float,
    drain_time_seconds: int,
    player_pp: int,
    expected_pp: int,
    accuracy: float,
    is_full_combo: bool = False,
    is_first_submission: bool = False,
    has_zero_fifty: bool = False,
    extra_challenge: bool = False,
) -> dict:
    """
    Calculates final HP using the full HPS 2.0 formula.
    
    Formula: Final HP = (Base HP × DM × LSS × RF) + Σ Bonuses
    
    Returns a dictionary with all calculated values for transparency.
    """
    base_hp = calculate_base_hp(result_type)
    dm = calculate_dynamic_dm(star_rating)
    lss = calculate_log_lss(drain_time_seconds)
    rf = calculate_relativity_factor(player_pp, expected_pp)
    bonuses = calculate_bonuses(
        accuracy=accuracy,
        is_full_combo=is_full_combo,
        is_first_submission=is_first_submission,
        has_zero_fifty=has_zero_fifty,
        extra_challenge=extra_challenge,
    )
    
    # Calculate final HP
    multiplier = dm * lss * rf
    final_hp = int((base_hp * multiplier) + bonuses)
    
    return {
        "base_hp": base_hp,
        "dm": round(dm, 3),
        "lss": round(lss, 3),
        "rf": round(rf, 3),
        "multiplier": round(multiplier, 3),
        "bonuses": bonuses,
        "final_hp": final_hp,
    }


# Example usage for testing
if __name__ == "__main__":
    result = calculate_final_hp(
        result_type="win",
        star_rating=6.82,
        drain_time_seconds=324,  # 5:24
        player_pp=1200,
        expected_pp=2800,
        accuracy=99.42,
        is_full_combo=True,
        is_first_submission=True,
        has_zero_fifty=False,
        extra_challenge=False,
    )
    
    print("=== HPS 2.0 Calculation ===")
    print(f"Base HP: {result['base_hp']}")
    print(f"Dynamic DM: x{result['dm']}")
    print(f"Log-LSS: x{result['lss']}")
    print(f"Relativity Factor: x{result['rf']}")
    print(f"Total Multiplier: x{result['multiplier']}")
    print(f"Bonuses: +{result['bonuses']} HP")
    print(f"=== FINAL HP: {result['final_hp']} ===")
