import math
from datetime import datetime


BASE_HP_TABLE = {
    "win": 100,
    "condition": 60,
    "partial": 30,
    "participation": 10,
    "sponsor": 20,
}



def calculate_dynamic_dm(star_rating: float) -> dict:

    dm = (star_rating * 0.24) - 0.16
    dm = max(0.8, min(2.0, dm))
    
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


def calculate_log_lss(drain_time_seconds: int) -> dict:

    drain_time_seconds = int(drain_time_seconds)
    
    if drain_time_seconds <= 0:
        drain_time_seconds = 30
    
    lss = 0.7 + (0.3 * math.log2(drain_time_seconds / 60 + 1))
    lss = max(0.7, min(2.0, lss))
    
    if drain_time_seconds < 120:
        category = "Sprint"
    elif drain_time_seconds < 270:
        category = "Standard"
    elif drain_time_seconds < 420:
        category = "Longer"
    elif drain_time_seconds < 600:
        category = "Marathon"
    else:
        category = "Titan"
    
    minutes = drain_time_seconds // 60
    seconds = drain_time_seconds % 60
    time_str = f"{minutes}:{seconds:02d}"
    
    return {
        "value": round(lss, 3),
        "category": category,
        "duration": time_str,
        "seconds": drain_time_seconds,
    }


def calculate_relativity_factor(player_pp: int, community_stats: dict) -> dict:

    p25 = community_stats.get("p25", 2000)
    p40 = community_stats.get("p40", 4500)
    p60 = community_stats.get("p60", 7000)
    p75 = community_stats.get("p75", 10000)
    
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
    
    rf = max(0.80, min(1.50, rf))
    
    return {
        "value": round(rf, 3),
        "category": category,
        "player_pp": player_pp,
    }


def calculate_bonuses(
    accuracy: float,
    is_full_combo: bool,
    is_first_submission: bool,
    has_zero_fifty: bool,
    extra_challenge: bool,
) -> dict:

    bonuses = []
    total = 0
    
    if accuracy >= 100.0:
        bonuses.append({"name": "Flawless Execution", "hp": 25})
        total += 25
    
    elif accuracy >= 99.0:
        bonuses.append({"name": "Elite Precision", "hp": 15})
        total += 15
    
    if is_first_submission:
        bonuses.append({"name": "Vanguard", "hp": 15})
        total += 15
    
    if has_zero_fifty:
        bonuses.append({"name": "Zero Fifty", "hp": 10})
        total += 10
    
    if extra_challenge:
        bonuses.append({"name": "Extra Challenge", "hp": 20})
        total += 20
    
    if total > 50:
        total = 50
        bonuses.append({"name": "Cap Applied", "hp": -1})
    
    return {
        "total": total,
        "list": bonuses,
    }


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
    
    base_hp = BASE_HP_TABLE.get(result_type.lower(), 10)
    
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
    
    total_multiplier = dm["value"] * lss["value"] * rf["value"]
    
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
