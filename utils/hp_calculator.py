import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# ur_estimator is imported lazily inside calculate_hps_v2 — eager import would
# resolve through utils/osu/__init__.py, which loads OsuApiClient, which itself
# imports get_rank_for_hp from this module, producing a circular import at
# load time.


RANK_THRESHOLDS = [
    (4500, "Big Brother"),
    (2000, "Commissioner"),
    (900,  "Inspector"),
    (300,  "Member"),
    (0,    "Candidate"),
]

# ── HPS v2 (Manifest) ranks ────────────────────────────────────────────────
# New thresholds from /home/naumredlo/HPS Balance Part III.  Used by callers
# that have migrated to calculate_hps_v2; the legacy RANK_THRESHOLDS above is
# kept until the backfill (#29) flips everyone over.
RANK_THRESHOLDS_V2 = [
    (3000, "Big Brother"),
    (1500, "High Commissioner"),
    (750,  "Inspector"),
    (250,  "Party Member"),
    (0,    "Candidate"),
]

MAX_HP_PER_SUBMISSION = 500

HPS_DIVISION_THRESHOLDS = [
    (7500, "Big Brother I"),
    (6000, "Big Brother II"),
    (4500, "Big Brother III"),
    (3667, "Commissioner I"),
    (2834, "Commissioner II"),
    (2000, "Commissioner III"),
    (1634, "Inspector I"),
    (1267, "Inspector II"),
    (900,  "Inspector III"),
    (700,  "Member I"),
    (500,  "Member II"),
    (300,  "Member III"),
    (200,  "Candidate I"),
    (100,  "Candidate II"),
    (0,    "Candidate III"),
]

BSK_DIVISION_THRESHOLDS = [
    (4300, "Rhythmus I"),
    (3800, "Rhythmus II"),
    (3300, "Rhythmus III"),
    (2900, "Virtuoso I"),
    (2500, "Virtuoso II"),
    (2100, "Virtuoso III"),
    (1800, "Challenger I"),
    (1500, "Challenger II"),
    (1200, "Challenger III"),
    (1000, "Contender I"),
    (800,  "Contender II"),
    (600,  "Contender III"),
    (400,  "Cadence I"),
    (200,  "Cadence II"),
    (0,    "Cadence III"),
]

BSK_DIVISION_INDEX = {d: i for i, (_, d) in enumerate(reversed(BSK_DIVISION_THRESHOLDS))}

SEASON_BONUS_HPS = {
    "Candidate III": 0,   "Candidate II": 10,  "Candidate I": 20,
    "Member III": 35,     "Member II": 50,     "Member I": 70,
    "Inspector III": 100, "Inspector II": 130, "Inspector I": 160,
    "Commissioner III": 200, "Commissioner II": 250, "Commissioner I": 300,
    "Big Brother III": 400, "Big Brother II": 500, "Big Brother I": 600,
}


def get_rank_for_hp(hp: int) -> str:
    for threshold, rank_name in RANK_THRESHOLDS:
        if hp >= threshold:
            return rank_name
    return "Candidate"


def get_rank_for_hp_v2(hp: int) -> str:
    """Return the v2 (Manifest) rank for a given HP total."""
    for threshold, rank_name in RANK_THRESHOLDS_V2:
        if hp >= threshold:
            return rank_name
    return "Candidate"


def get_next_rank_info_v2(hp: int) -> dict:
    current_rank = get_rank_for_hp_v2(hp)
    for i, (threshold, rank_name) in enumerate(RANK_THRESHOLDS_V2):
        if hp >= threshold:
            if i == 0:
                return {"current": current_rank, "next": None, "hp_needed": 0}
            next_threshold, next_rank = RANK_THRESHOLDS_V2[i - 1]
            return {
                "current": current_rank,
                "next": next_rank,
                "hp_needed": next_threshold - hp,
            }
    return {"current": "Candidate", "next": "Party Member", "hp_needed": 250 - hp}


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
    return {"current": "Candidate", "next": "Member", "hp_needed": 300 - hp}


def get_division_for_hp(hp: int) -> str:
    for threshold, division in HPS_DIVISION_THRESHOLDS:
        if hp >= threshold:
            return division
    return "Candidate III"


def get_division_for_conservative(conservative: float) -> str:
    for threshold, division in BSK_DIVISION_THRESHOLDS:
        if conservative >= threshold:
            return division
    return "Cadence III"


BASE_HP_TABLE = {
    "win":           150,
    "condition":      90,
    "partial":        45,
    "participation":  20,
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
    final_hp = min(final_hp, MAX_HP_PER_SUBMISSION)

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


# ═════════════════════════════════════════════════════════════════════════════
# HPS v2 — Math Manifest implementation
#
# Source: /home/naumredlo/HPS Balance (Part II).  Mounted alongside the legacy
# calculate_hps so we can dry-run / backfill before flipping callers.  Once
# auto_checker and /submit start writing v2 results to Submission.hp_awarded
# (#30), the legacy block above becomes dead code and can be deleted (#32).
# ═════════════════════════════════════════════════════════════════════════════

# Default tunables — calibrated against the dry-run report (#28).
HPS_V2_BASE = 60
HPS_V2_VANGUARD = 25

RESULT_TYPE_MULTIPLIER = {
    "win":           1.5,
    "condition":     1.0,
    "partial":       0.5,
    "participation": 0.2,
}


def _phi(bsk_map: float) -> float:
    """Φ(BSK) = 0.5 + 0.05·BSK^1.8  — map difficulty multiplier."""
    if bsk_map <= 0:
        return 0.5
    return 0.5 + 0.05 * (bsk_map ** 1.8)


def _psi(delta: float) -> float:
    """Ψ(Δ) = 0.5 + 1.5 / (1 + exp(-1.5·Δ))  — skill-relative multiplier.

    Δ = BSK_map − BSK_user (positive when map is harder than the player).
    Range: 0.5 (Δ → −∞, deep farming) to 2.0 (Δ → +∞, way over their head).
    """
    return 0.5 + 1.5 / (1.0 + math.exp(-1.5 * delta))


def _omega(ur_est: Optional[float]) -> float:
    """Ω(UR) = exp((100 − UR) / 75)  — tap-timing multiplier.

    Defaults to 1.0 when UR is unavailable (historical submissions, partial
    plays with N_hits = 0).  Caller can decide whether None should suppress
    the modifier; we centralize the "neutral" semantics here.
    """
    if ur_est is None:
        return 1.0
    return math.exp((100.0 - ur_est) / 75.0)


def _lambda(drain_time_seconds: int) -> float:
    """Λ(t) = max(0.4, ln(1 + t/150) + 0.6)  — length scaling."""
    t = max(0, int(drain_time_seconds or 0))
    return max(0.4, math.log(1.0 + t / 150.0) + 0.6)


def _c_pen(combo: int, max_combo: int, misses: int) -> float:
    """C_pen = sqrt(combo / max_combo) · 0.92^misses."""
    if max_combo and max_combo > 0:
        ratio = max(0.0, min(1.0, combo / max_combo))
        combo_factor = math.sqrt(ratio)
    else:
        combo_factor = 1.0
    miss_factor = 0.92 ** max(0, int(misses or 0))
    return combo_factor * miss_factor


@dataclass(slots=True)
class MapInfo:
    """What calculate_hps_v2 needs to know about the beatmap.

    Pulled from `bsk_map_pool` when available; otherwise constructed from
    `Bounty` fields with all four axes equal to the overall star rating.
    """
    aim_stars: float
    speed_stars: float
    acc_stars: float
    cons_stars: float
    w_aim: float
    w_speed: float
    w_acc: float
    w_cons: float
    od: float
    drain_time_seconds: int
    max_combo: int

    @classmethod
    def fallback_from_sr(cls, *, star_rating: float, od: float, drain_time: int, max_combo: int) -> "MapInfo":
        return cls(
            aim_stars=star_rating, speed_stars=star_rating,
            acc_stars=star_rating, cons_stars=star_rating,
            w_aim=0.25, w_speed=0.25, w_acc=0.25, w_cons=0.25,
            od=od, drain_time_seconds=drain_time, max_combo=max_combo,
        )


@dataclass(slots=True)
class PlayerSkill:
    aim: float
    speed: float
    acc: float
    cons: float


@dataclass(slots=True)
class ScoreStats:
    n_300: int
    n_100: int
    n_50: int
    misses: int
    combo: int
    mods: object = None  # passed through to ur_estimator; accepts str / list / None


def _bsk_map_and_delta(map_info: MapInfo, player: PlayerSkill) -> tuple[float, float]:
    """Weighted composite of map difficulty and the per-axis skill gap."""
    w = (map_info.w_aim, map_info.w_speed, map_info.w_acc, map_info.w_cons)
    s = (map_info.aim_stars, map_info.speed_stars, map_info.acc_stars, map_info.cons_stars)
    p = (player.aim, player.speed, player.acc, player.cons)
    bsk_map = sum(wi * si for wi, si in zip(w, s))
    delta   = sum(wi * (si - pi) for wi, si, pi in zip(w, s, p))
    return bsk_map, delta


def calculate_hps_v2(
    *,
    result_type: str,
    map_info: MapInfo,
    player_skill: PlayerSkill,
    score: ScoreStats,
    is_first_submission: bool = False,
    base: int = HPS_V2_BASE,
    vanguard_hp: int = HPS_V2_VANGUARD,
    ur_est_override: Optional[float] = None,
) -> dict:
    """Compute HP_final per the HPS Math Manifest (Part II).

    `ur_est_override` lets the caller supply a pre-computed UR (e.g. from a
    stored `submission.ur_est`); when omitted we recompute it from the score
    stats and the map's OD/mods.
    """
    ur_est = ur_est_override
    if ur_est is None:
        from utils.osu.ur_estimator import estimate_ur  # lazy — see top-of-file note
        ur_est = estimate_ur(
            score.n_300, score.n_100, score.n_50,
            od=map_info.od, mods=score.mods,
        )

    bsk_map, delta = _bsk_map_and_delta(map_info, player_skill)
    phi    = _phi(bsk_map)
    psi    = _psi(delta)
    omega  = _omega(ur_est)
    lam    = _lambda(map_info.drain_time_seconds)
    c_pen  = _c_pen(score.combo, map_info.max_combo, score.misses)
    r_mult = RESULT_TYPE_MULTIPLIER.get((result_type or "").lower(), 0.0)

    hp_pre = base * phi * psi * omega * lam * c_pen * r_mult
    vanguard = vanguard_hp if is_first_submission else 0
    final_hp = max(0, math.floor(hp_pre + vanguard))

    return {
        "base":     base,
        "phi":      round(phi, 4),
        "psi":      round(psi, 4),
        "omega":    round(omega, 4),
        "lambda":   round(lam, 4),
        "c_pen":    round(c_pen, 4),
        "r":        r_mult,
        "vanguard": vanguard,
        "ur_est":   round(ur_est, 2) if ur_est is not None else None,
        "bsk_map":  round(bsk_map, 3),
        "delta":    round(delta, 3),
        "hp_pre":   round(hp_pre, 2),
        "final_hp": final_hp,
        "calculated_at": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S"),
    }
