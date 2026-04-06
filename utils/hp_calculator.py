import math
from datetime import datetime, timezone


RANK_THRESHOLDS = [
    (3001, "Big Brother"),
    (1501, "High Commissioner"),
    (751, "Inspector"),
    (251, "Party Member"),
    (0, "Candidate"),
]


def get_rank_for_hp(hp: int) -> str:
    for threshold, rank_name in RANK_THRESHOLDS:
        if hp >= threshold:
            return rank_name
    return "Candidate"


def get_next_rank_info(hp: int) -> dict:
    current_rank = get_rank_for_hp(hp)
    for i, (threshold, rank_name) in enumerate(RANK_THRESHOLDS):
        if hp >= threshold:
            if i == 0:
                return {"current": current_rank, "next": None, "hp_needed": 0}
            next_threshold, next_rank = RANK_THRESHOLDS[i - 1]
            return {
                "current": current_rank,
                "next": next_rank,
                "hp_needed": next_threshold - hp,
            }
    return {"current": "Candidate", "next": "Party Member", "hp_needed": 251 - hp}


BASE_HP_TABLE = {
    "win": 100,
    "condition": 60,
    "partial": 30,
    "participation": 10,
    "sponsor": 20,
}



def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def calculate_tsf(cs: float, od: float, ar: float, hp: float, bpm: float, max_combo: int) -> dict:
    has_data = any(v > 0 for v in [cs, od, ar, hp, bpm, max_combo])
    if not has_data:
        return {
            "value": 1.0,
            "cs": 1.0, "od": 1.0, "ar": 1.0,
            "hp": 1.0, "bpm": 1.0, "combo": 1.0,
        }

    tsf_cs = _clamp(0.80 + (cs / 7.0) * 0.50, 0.85, 1.35)
    tsf_od = _clamp(0.80 + (od / 10.0) * 0.50, 0.85, 1.35)
    tsf_ar = _clamp(1.0 + abs(ar - 9.0) * 0.15, 1.00, 1.35)
    tsf_hp = _clamp(0.90 + (hp / 10.0) * 0.30, 0.90, 1.20)
    tsf_bpm = _clamp(0.80 + (bpm / 300.0) * 0.50, 0.90, 1.35)
    tsf_combo = _clamp(0.90 + (max_combo / 2000.0) * 0.30, 0.90, 1.25)

    product = tsf_cs * tsf_od * tsf_ar * tsf_hp * tsf_bpm * tsf_combo
    tsf = product ** (1 / 6)

    return {
        "value": round(tsf, 3),
        "cs": round(tsf_cs, 3),
        "od": round(tsf_od, 3),
        "ar": round(tsf_ar, 3),
        "hp": round(tsf_hp, 3),
        "bpm": round(tsf_bpm, 3),
        "combo": round(tsf_combo, 3),
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

    p25 = community_stats.get("p25", 0)
    p40 = community_stats.get("p40", 0)
    p60 = community_stats.get("p60", 0)
    p75 = community_stats.get("p75", 0)

    if p75 == 0 and p25 == 0:
        rf = 1.0
        category = "Average"
    elif player_pp >= p75:
        rf = 0.80
        category = "Top Player"
    elif player_pp >= p60:
        rf = 0.90
        category = "Above Average"
    elif player_pp >= p40:
        rf = 1.00
        category = "Average"
    elif player_pp >= p25:
        rf = 1.10
        category = "Below Average"
    else:
        rf = 1.20
        category = "Newcomer"

    return {
        "value": rf,
        "category": category,
        "player_pp": player_pp,
    }


def calculate_bonuses(
    accuracy: float,
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
    is_first_submission: bool = False,
    has_zero_fifty: bool = False,
    extra_challenge: bool = False,
    cs: float = 0.0,
    od: float = 0.0,
    ar: float = 0.0,
    hp_drain: float = 0.0,
    bpm: float = 0.0,
    max_combo: int = 0,
) -> dict:

    base_hp = BASE_HP_TABLE.get(result_type.lower(), 10)

    dm = calculate_dynamic_dm(star_rating)
    lss = calculate_log_lss(drain_time_seconds)
    rf = calculate_relativity_factor(player_pp, community_stats)
    tsf = calculate_tsf(cs, od, ar, hp_drain, bpm, max_combo)
    bonuses = calculate_bonuses(
        accuracy=accuracy,
        is_first_submission=is_first_submission,
        has_zero_fifty=has_zero_fifty,
        extra_challenge=extra_challenge,
    )

    total_multiplier = dm["value"] * lss["value"] * rf["value"] * tsf["value"]

    final_hp = int((base_hp * total_multiplier) + bonuses["total"])

    return {
        "base_hp": base_hp,
        "dynamic_dm": dm,
        "log_lss": lss,
        "relativity_factor": rf,
        "tsf": tsf,
        "total_multiplier": round(total_multiplier, 3),
        "bonuses": bonuses,
        "final_hp": final_hp,
        "calculated_at": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S"),
    }
