# utils/hp_calculator.py
"""
Hunter Progression System (HPS) 2.0 Calculator
Full HP calculation formula for 1984 Bounties Competitive system.
"""

import math
from datetime import datetime


# ← 1. BASE HP (Base points for result)
BASE_HP_TABLE = {
    "win": 100,           # Victory
    "condition": 60,      # Condition met (FC, SS, etc.)
    "partial": 30,        # Partially completed
    "participation": 10,  # Participation
    "sponsor": 20,        # Sponsorship
}


# ← 2. DYNAMIC DM (Map difficulty - linear formula)
def calculate_dynamic_dm(star_rating: float) -> dict:
    """
    Calculate map difficulty multiplier.
    Formula: DM = (StarRating × 0.24) - 0.16
    Range: 4★ (x0.80) → 9★ (x2.00)
    """
    dm = (star_rating * 0.24) - 0.16
    dm = max(0.8, min(2.0, dm))  # Limits
    
    # Determine category
    if star_rating < 5.0:
        category = "Beginner"
    elif star_rating < 6.0:
        category = "Basic"
    elif star_rating < 7.0:
        category = "Advanced"
    elif star_rating < 8.0:
        category = "Expert"
    else:
        category = "Legendary"
    
    return {
        "value": round(dm, 3),
        "category": category,
        "stars": star_rating,
    }


# ← 3. LOG-LSS (Map duration - logarithmic formula)
def calculate_log_lss(drain_time_seconds: int) -> dict:
    """
    Calculate map duration multiplier.
    Formula: LSS = 0.7 + (0.3 × log₂(DrainTimeSeconds / 60 + 1))
    Range: 0:30 (x0.875) → 20:00 (x2.00)
    Uses total map length (not drain time).
    """
    # Convert to integer
    drain_time_seconds = int(drain_time_seconds)
    
    if drain_time_seconds <= 0:
        drain_time_seconds = 30  # Minimum 30 seconds
    
    lss = 0.7 + (0.3 * math.log2(drain_time_seconds / 60 + 1))
    lss = max(0.7, min(2.0, lss))  # Limits
    
    # Determine category
    if drain_time_seconds < 120:  # < 2:00
        category = "Sprint"
    elif drain_time_seconds < 270:  # < 4:30
        category = "Standard"
    elif drain_time_seconds < 420:  # < 7:00
        category = "Longer"
    elif drain_time_seconds < 600:  # < 10:00
        category = "Marathon"
    else:
        category = "Titan"
    
    # Format time
    minutes = drain_time_seconds // 60
    seconds = drain_time_seconds % 60
    time_str = f"{minutes}:{seconds:02d}"
    
    return {
        "value": round(lss, 3),
        "category": category,
        "duration": time_str,
        "seconds": drain_time_seconds,
    }


# ← 4. RELATIVITY FACTOR (Player progress - community percentiles)
def calculate_relativity_factor(player_pp: int, community_stats: dict) -> dict:
    """
    Calculate relative progress multiplier.
    Based on community percentile thresholds.
    """
    p25 = community_stats.get("p25", 500)
    p40 = community_stats.get("p40", 1000)
    p60 = community_stats.get("p60", 2500)
    p75 = community_stats.get("p75", 4000)
    
    if player_pp >= p75:
        rf = 0.80
        category = "Top Player"
    elif player_pp >= p60:
        rf = 0.90
        category = "Above Average"
    elif player_pp >= p40:
        rf = 1.00
        category = "Average"
    elif player_pp >= p25:
        rf = 1.15
        category = "Below Average"
    else:
        rf = 1.30
        category = "Newcomer"
    
    rf = max(0.80, min(1.50, rf))  # Limits
    
    return {
        "value": round(rf, 3),
        "category": category,
        "player_pp": player_pp,
    }


# ← 5. BONUSES (Σ Bonuses)
def calculate_bonuses(
    accuracy: float,
    is_full_combo: bool,
    is_first_submission: bool,
    has_zero_fifty: bool,
    extra_challenge: bool,
) -> dict:
    """
    Calculate bonus sum.
    Maximum: +50 HP
    """
    bonuses = []
    total = 0
    
    # Flawless Execution (100%)
    if accuracy >= 100.0:
        bonuses.append({"name": "Flawless Execution", "hp": 25})
        total += 25
    # Elite Precision (≥99%)
    elif accuracy >= 99.0:
        bonuses.append({"name": "Elite Precision", "hp": 15})
        total += 15
    
    # Vanguard (first submission)
    if is_first_submission:
        bonuses.append({"name": "Vanguard", "hp": 15})
        total += 15
    
    # Zero Fifty
    if has_zero_fifty:
        bonuses.append({"name": "Zero Fifty", "hp": 10})
        total += 10
    
    # Extra Challenge
    if extra_challenge:
        bonuses.append({"name": "Extra Challenge", "hp": 20})
        total += 20
    
    # Cap bonuses
    if total > 50:
        total = 50
        bonuses.append({"name": "Cap Applied", "hp": -1})  # Marker
    
    return {
        "total": total,
        "list": bonuses,
    }


# ← 6. FINAL FORMULA
def calculate_hps(
    result_type: str,
    star_rating: float,
    drain_time_seconds: int,
    player_pp: int,
    community_stats: dict,
    accuracy: float = 0.0,
    is_full_combo: bool = False,
    is_first_submission: bool = False,
    has_zero_fifty: bool = False,
    extra_challenge: bool = False,
) -> dict:
    """
    Full HPS 2.0 formula.
    
    Final HP = (Base HP × DM × LSS × RF) + Σ Bonuses
    
    Returns:
        dict with full breakdown of all multipliers
    """
    # Base points
    base_hp = BASE_HP_TABLE.get(result_type.lower(), 10)
    
    # Multipliers
    dm = calculate_dynamic_dm(star_rating)
    lss = calculate_log_lss(drain_time_seconds)
    rf = calculate_relativity_factor(player_pp, community_stats)
    bonuses = calculate_bonuses(
        accuracy=accuracy,
        is_full_combo=is_full_combo,
        is_first_submission=is_first_submission,
        has_zero_fifty=has_zero_fifty,
        extra_challenge=extra_challenge,
    )
    
    # Total multiplier
    total_multiplier = dm["value"] * lss["value"] * rf["value"]
    
    # Final HP
    final_hp = int((base_hp * total_multiplier) + bonuses["total"])
    
    return {
        "base_hp": base_hp,
        "dynamic_dm": dm,
        "log_lss": lss,
        "relativity_factor": rf,
        "total_multiplier": round(total_multiplier, 3),
        "bonuses": bonuses,
        "final_hp": final_hp,
        "calculated_at": datetime.utcnow().strftime("%d.%m.%Y %H:%M:%S"),
    }
