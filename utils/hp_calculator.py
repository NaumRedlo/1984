import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


RANK_THRESHOLDS = [
    (3000, "Big Brother"),
    (1500, "Commissioner"),
    (750,  "Inspector"),
    (250,  "Member"),
    (0,    "Candidate"),
]

# ── Bounty tier mapping ────────────────────────────────────────────────────
# Groups the 5 v2 ranks into 3 tiers used by the weekly bounty pool generator.
# See plan: services/bounty/weekly_generator.py picks 9 maps per tier from
# bsk_map_pool, plus 9 Open.  Tier is the *player's* bucket; Open is visible
# to everyone regardless of tier.  This is the single source of truth — any
# module that needs to know a user's tier must call get_tier_for_hp().
#
# Boundaries (in HPS points, by rank threshold):
#   Tier C  =  Candidate (0–249) + Member (250–749)            → 0–749
#   Tier B  =  Inspector (750–1499)                                   → 750–1499
#   Tier A  =  Commissioner (1500–2999) + Big Brother (3000+)   → 1500+
RANK_TO_TIER = {
    "Candidate":         "C",
    "Member":      "C",
    "Inspector":         "B",
    "Commissioner": "A",
    "Big Brother":       "A",
}


def get_tier_for_hp(hp: int) -> str:
    """Map HPS points → bounty tier ('C' | 'B' | 'A')."""
    return RANK_TO_TIER[get_rank_for_hp(hp)]

MAX_HP_PER_SUBMISSION = 500

HPS_DIVISION_THRESHOLDS = [
    (6000, "Big Brother I"),
    (4500, "Big Brother II"),
    (3000, "Big Brother III"),
    (2500, "Commissioner I"),
    (2000, "Commissioner II"),
    (1500, "Commissioner III"),
    (1250, "Inspector I"),
    (1000, "Inspector II"),
    (750,  "Inspector III"),
    (583,  "Member I"),
    (417,  "Member II"),
    (250,  "Member III"),
    (167,  "Candidate I"),
    (84,   "Candidate II"),
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
    return {"current": "Candidate", "next": "Member", "hp_needed": 250 - hp}


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





# Default tunables.
HPS_BASE = 60
HPS_VANGUARD = 25

RESULT_TYPE_MULTIPLIER = {
    "win":           1.5,
    "condition":     1.0,
    "partial":       0.5,
    "participation": 0.2,
}

# Bounty-type scaling applied on top of the base formula.
# Harder task types push the payout higher, up to the per-submission cap.
BOUNTY_TYPE_MULTIPLIER: dict[str, float] = {
    "SS":         1.6,
    "Metronome":  1.4,
    "Accuracy":   1.2,
    "Marathon":   1.2,
    "Mod":        1.1,
    "Pass":       1.0,
    "First FC":   1.0,
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
    """What calculate_hps needs to know about the beatmap.

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
    mods: object = None  # passed through for future .osr-based UR; accepts str / list / None


def _bsk_map_and_delta(map_info: MapInfo, player: PlayerSkill) -> tuple[float, float]:
    """Weighted composite of map difficulty and the per-axis skill gap."""
    w = (map_info.w_aim, map_info.w_speed, map_info.w_acc, map_info.w_cons)
    s = (map_info.aim_stars, map_info.speed_stars, map_info.acc_stars, map_info.cons_stars)
    p = (player.aim, player.speed, player.acc, player.cons)
    bsk_map = sum(wi * si for wi, si in zip(w, s))
    delta   = sum(wi * (si - pi) for wi, si, pi in zip(w, s, p))
    return bsk_map, delta


def calculate_hps(
    *,
    result_type: str,
    map_info: MapInfo,
    player_skill: PlayerSkill,
    score: ScoreStats,
    is_first_submission: bool = False,
    base: int = HPS_BASE,
    vanguard_hp: int = HPS_VANGUARD,
    ur_est_override: Optional[float] = None,
    bounty_type: Optional[str] = None,
) -> dict:
    """Compute HP_final per the HPS Math Manifest (Part II).

    `ur_est_override` accepts real UR parsed from a .osr replay file.
    `bounty_type` applies BOUNTY_TYPE_MULTIPLIER before the per-submission cap.
    """
    ur_est = ur_est_override

    bsk_map, delta = _bsk_map_and_delta(map_info, player_skill)
    phi    = _phi(bsk_map)
    psi    = _psi(delta)
    omega  = _omega(ur_est)
    lam    = _lambda(map_info.drain_time_seconds)
    c_pen  = _c_pen(score.combo, map_info.max_combo, score.misses)
    r_mult = RESULT_TYPE_MULTIPLIER.get((result_type or "").lower(), 0.0)
    t_mult = BOUNTY_TYPE_MULTIPLIER.get(bounty_type or "", 1.0)

    hp_pre = base * phi * psi * omega * lam * c_pen * r_mult * t_mult
    hp_pre_capped = min(hp_pre, MAX_HP_PER_SUBMISSION)
    vanguard = vanguard_hp if is_first_submission else 0
    final_hp = max(0, math.floor(hp_pre_capped + vanguard))

    return {
        "base":          base,
        "phi":           round(phi, 4),
        "psi":           round(psi, 4),
        "omega":         round(omega, 4),
        "lambda":        round(lam, 4),
        "c_pen":         round(c_pen, 4),
        "r":             r_mult,
        "t":             t_mult,
        "vanguard":      vanguard,
        "ur_est":        round(ur_est, 2) if ur_est is not None else None,
        "bsk_map":       round(bsk_map, 3),
        "delta":         round(delta, 3),
        "hp_pre":        round(hp_pre, 2),
        "capped":        hp_pre > MAX_HP_PER_SUBMISSION,
        "final_hp":      final_hp,
        "calculated_at": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S"),
    }
