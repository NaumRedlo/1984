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
# duel_map_pool, plus 9 Open.  Tier is the *player's* bucket; Open is visible
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

# Soft-cap asymptote: tanh compression. HP never literally reaches this value
# but approaches it as hp_pre → ∞. Replaces the old hard floor (500 HP) so
# extreme difficulty wins score meaningfully above medium wins.
HP_SOFT_CAP = 600

HPS_DIVISION_THRESHOLDS = [
    # Big Brother: 3000+, step 500; BB II→I gap doubles to 1000 (final push)
    (5000, "Big Brother I"),
    (4000, "Big Brother II"),
    (3500, "Big Brother III"),
    (3000, "Big Brother IV"),
    # Commissioner: 1500–2999, step 375
    (2625, "Commissioner I"),
    (2250, "Commissioner II"),
    (1875, "Commissioner III"),
    (1500, "Commissioner IV"),
    # Inspector: 750–1499, step 200
    (1350, "Inspector I"),
    (1150, "Inspector II"),
    (950,  "Inspector III"),
    (750,  "Inspector IV"),
    # Member: 250–749, step 125
    (625,  "Member I"),
    (500,  "Member II"),
    (375,  "Member III"),
    (250,  "Member IV"),
    # Candidate: 0–249, step 60
    (180,  "Candidate I"),
    (120,  "Candidate II"),
    (60,   "Candidate III"),
    (0,    "Candidate IV"),
]

DUEL_DIVISION_THRESHOLDS = [
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

DUEL_DIVISION_INDEX = {d: i for i, (_, d) in enumerate(reversed(DUEL_DIVISION_THRESHOLDS))}

SEASON_BONUS_HPS = {
    "Candidate IV": 0,   "Candidate III": 8,   "Candidate II": 15,  "Candidate I": 22,
    "Member IV": 35,     "Member III": 50,      "Member II": 62,     "Member I": 78,
    "Inspector IV": 95,  "Inspector III": 120,  "Inspector II": 145, "Inspector I": 165,
    "Commissioner IV": 185, "Commissioner III": 225, "Commissioner II": 265, "Commissioner I": 305,
    "Big Brother IV": 390, "Big Brother III": 480, "Big Brother II": 570, "Big Brother I": 680,
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
    for threshold, division in DUEL_DIVISION_THRESHOLDS:
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


def _phi(duel_map: float) -> float:
    """Φ(DUEL) = 0.5 + 0.05·DUEL^1.8  — map difficulty multiplier."""
    if duel_map <= 0:
        return 0.5
    return 0.5 + 0.05 * (duel_map ** 1.8)


def _psi(delta: float) -> float:
    """Ψ(Δ) = 0.5 + 1.5 / (1 + exp(-1.5·Δ))  — skill-relative multiplier.

    Δ = DUEL_map − DUEL_user (positive when map is harder than the player).
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


def _c_pen(combo: int, max_combo: int, *, miss_rate: float = 0.0) -> float:
    """C_pen = sqrt(combo / max_combo) · exp(-3 · miss_rate)

    miss_rate = misses / total_hits — proportional penalty: 10 misses on a
    2000-note map is much lighter than 10 misses on a 100-note map.
    Caller computes miss_rate; default 0.0 means no miss penalty.
    """
    if max_combo and max_combo > 0:
        ratio = max(0.0, min(1.0, combo / max_combo))
        combo_factor = math.sqrt(ratio)
    else:
        combo_factor = 1.0
    return combo_factor * math.exp(-3.0 * max(0.0, miss_rate))


@dataclass(slots=True)
class MapInfo:
    """What calculate_hps needs to know about the beatmap.

    Pulled from `duel_map_pool` when available; otherwise constructed from
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


def _duel_map_and_delta(map_info: MapInfo, player: PlayerSkill) -> tuple[float, float]:
    """Weighted composite of map difficulty and the per-axis skill gap."""
    w = (map_info.w_aim, map_info.w_speed, map_info.w_acc, map_info.w_cons)
    s = (map_info.aim_stars, map_info.speed_stars, map_info.acc_stars, map_info.cons_stars)
    p = (player.aim, player.speed, player.acc, player.cons)
    duel_map = sum(wi * si for wi, si in zip(w, s))
    delta   = sum(wi * (si - pi) for wi, si, pi in zip(w, s, p))
    return duel_map, delta


def _psi_hybrid(map_info: MapInfo, player: PlayerSkill) -> tuple[float, float, float]:
    """Per-axis Ψ blend: max(Ψ_axis) × 0.7 + Σ w_axis · Ψ_axis × 0.3.

    Rationale (plan: unified-giggling-tiger):
      The legacy single Ψ(Σ w·Δ) averages the skill gap across all four
      axes.  A specialist (aim 8.0, speed 2.0) attempting a speed-heavy
      map gets the same Ψ as a balanced 5/5/5/5 player even though the
      speed gap is brutal for them.  The hybrid weights the max-axis Ψ
      heavily (0.7) so the hardest demand dominates, with a 0.3 floor
      from the weighted average so balanced players aren't ignored.

    Returns (psi_hybrid, psi_max, psi_avg) — last two for breakdown.
    """
    w = (map_info.w_aim, map_info.w_speed, map_info.w_acc, map_info.w_cons)
    s = (map_info.aim_stars, map_info.speed_stars, map_info.acc_stars, map_info.cons_stars)
    p = (player.aim, player.speed, player.acc, player.cons)
    deltas    = [si - pi for si, pi in zip(s, p)]
    psi_axes  = [_psi(d) for d in deltas]
    psi_max   = max(psi_axes)
    psi_avg   = sum(wi * pi for wi, pi in zip(w, psi_axes))
    return 0.7 * psi_max + 0.3 * psi_avg, psi_max, psi_avg


# ── Bootstrap multiplier B(t) ───────────────────────────────────────────────
#
# Anchored on User.first_approved_at (NOT account creation). Starts at ~1.5
# on day 0 and decays through a sigmoid back to ~1.0 by day 60+. Helps new
# HPS-active users climb out of the candidate tier without warping the
# economy for long-term players.

BOOTSTRAP_PEAK     = 0.5     # +50 % bonus on day 0
BOOTSTRAP_MIDPOINT = 30      # sigmoid centre, days
BOOTSTRAP_SLOPE    = 15.0    # sigmoid width, days


def _bootstrap_multiplier(days_since_first_approved: Optional[int]) -> float:
    """B(t) = 1 + 0.5 · sigmoid(−(t − 30) / 15).

    Day 0 → ≈ 1.49 (peak), day 30 → 1.25, day 60 → ≈ 1.04, day 90 → ≈ 1.0.
    Returns 1.0 when `days_since_first_approved` is None — used when the
    user has no approvals yet and the caller didn't pre-compute the days.
    """
    if days_since_first_approved is None:
        return 1.0
    t = float(days_since_first_approved)
    return 1.0 + BOOTSTRAP_PEAK / (1.0 + math.exp((t - BOOTSTRAP_MIDPOINT) / BOOTSTRAP_SLOPE))


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
    anti_farm_multiplier: float = 1.0,
    bootstrap_multiplier: float = 1.0,
    use_psi_hybrid: bool = True,
    days_since_first_approved: Optional[int] = None,
) -> dict:
    """Compute HP_final per the HPS Math Manifest (Part II).

    `ur_est_override` accepts real UR parsed from a .osr replay file.
    `bounty_type` applies BOUNTY_TYPE_MULTIPLIER before the per-submission cap.

    New (plan: unified-giggling-tiger, step 7):
      * `anti_farm_multiplier` — F_repeat from services.hps.anti_farm
        (0.3..1.0). Penalty for repeated maps/types.
      * `bootstrap_multiplier` — B(t) anchor for HPS-career age. Caller
        may either pass this pre-computed OR pass
        `days_since_first_approved` and let us compute it via
        `_bootstrap_multiplier`. If both are passed the explicit
        multiplier wins.
      * `use_psi_hybrid` — toggle for the hybrid Ψ blend. Default True;
        set False to reproduce the legacy single-Ψ behaviour (used by
        the dryrun --legacy-multipliers regression baseline).
    """
    ur_est = ur_est_override

    duel_map, delta = _duel_map_and_delta(map_info, player_skill)
    phi   = _phi(duel_map)
    if use_psi_hybrid:
        psi, psi_max, psi_avg = _psi_hybrid(map_info, player_skill)
    else:
        psi = _psi(delta)
        psi_max = psi_avg = psi
    omega = 1.0  # UR is enforced as a bounty condition (max_ur); not a formula multiplier
    lam   = _lambda(map_info.drain_time_seconds)

    total_hits = score.n_300 + score.n_100 + score.n_50 + score.misses
    miss_rate  = score.misses / max(1, total_hits)
    c_pen  = _c_pen(score.combo, map_info.max_combo, miss_rate=miss_rate)

    r_mult = RESULT_TYPE_MULTIPLIER.get((result_type or "").lower(), 0.0)
    t_mult = BOUNTY_TYPE_MULTIPLIER.get(bounty_type or "", 1.0)

    # Resolve bootstrap: explicit value wins; otherwise derive from days.
    if bootstrap_multiplier == 1.0 and days_since_first_approved is not None:
        bootstrap_multiplier = _bootstrap_multiplier(days_since_first_approved)

    # Clamp incoming multipliers defensively — caller bugs shouldn't blow
    # up the economy.  Anti-farm honours its own [0.3, 1.0] floor; bootstrap
    # is bounded to [1.0, 1.5] by construction but we cap at 2.0 for safety.
    af_m = max(0.0, min(1.0, anti_farm_multiplier))
    bt_m = max(1.0, min(2.0, bootstrap_multiplier))

    hp_pre = base * phi * psi * omega * lam * c_pen * r_mult * t_mult * af_m * bt_m
    # Soft cap via tanh: same asymptote as the old hard cap but with smooth
    # compression — DUEL=9 wins score meaningfully more than DUEL=6 wins.
    hp_compressed = HP_SOFT_CAP * math.tanh(hp_pre / HP_SOFT_CAP)
    vanguard = vanguard_hp if is_first_submission else 0
    final_hp = max(0, math.floor(hp_compressed + vanguard))

    return {
        "base":          base,
        "phi":           round(phi, 4),
        "psi":           round(psi, 4),
        "psi_hybrid_max": round(psi_max, 4),
        "psi_hybrid_avg": round(psi_avg, 4),
        "omega":         1.0,
        "lambda":        round(lam, 4),
        "c_pen":         round(c_pen, 4),
        "r":             r_mult,
        "t":             t_mult,
        "anti_farm":     round(af_m, 4),
        "bootstrap":     round(bt_m, 4),
        "days_since_first_approved": days_since_first_approved,
        "vanguard":      vanguard,
        "ur_est":        round(ur_est, 2) if ur_est is not None else None,
        "duel_map":       round(duel_map, 3),
        "delta":         round(delta, 3),
        "hp_pre":        round(hp_pre, 2),
        "capped":        hp_pre > HP_SOFT_CAP,
        "final_hp":      final_hp,
        "calculated_at": datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M:%S"),
    }
