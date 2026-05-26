"""Tier-based bounty pool rules — pure logic, no IO.

Plan: unified-giggling-tiger.

This module is intentionally side-effect free so it can be unit-tested in
isolation. The weekly generator (`services.bounty.weekly_generator`) wires
this against `BskMapPool` rows and the `bounties` table.

Public API
----------
TIER_BSK_RANGES : dict[str, tuple[float, float]]
    BSK_map composite ranges per tier. Open spans the full skill space.
    Defaults are theoretical; refine after first dry-run on real data.

compute_bsk_map(map_row) -> float
    Composite: Σ w_axis · axis_stars, fallback to star_rating for off-pool
    rows missing per-axis values.

pick_for_tier(maps, tier, n=9) -> list
    Filter `maps` by `TIER_BSK_RANGES[tier]` and return up to `n` rows.
    Sort: closest-to-tier-midpoint first (so the selected slice represents
    the tier's "center of mass" instead of just its boundaries).

assign_bounty_type(map_row, tier) -> tuple[str, dict]
    Apply BOUNTY_TYPE_RULES in order, return first matching
    (bounty_type, conditions_dict). Fallback is ("First FC", {}).

The conditions dict is JSON-serialised into Bounty.conditions, and any keys
that have a legacy column mirror (min_accuracy, required_mods, max_misses)
are also written to those columns so bounty_auto_checker keeps working
without changes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Callable


# ── Tier ranges over BSK_map (composite, [0..10]) ──────────────────────────
# Calibrated from the live pool audit (10050 maps, May 2026): BSK_map
# (axis-mean) has mean ≈ 2.14, p40 ≈ 1.70, p66 ≈ 2.65, p90 ≈ 3.79. Earlier
# ranges (0..4.5 / 4.5..6.5 / 6.5..10) were on the star_rating scale and
# left A-tier with only 21 eligible maps. The current split distributes
# eligible maps ~40% / ~26% / ~34% across C/B/A.
TIER_BSK_RANGES: dict[str, tuple[float, float]] = {
    "C":    (0.0,  1.70),
    "B":    (1.70, 2.65),
    "A":    (2.65, 10.0),
    "Open": (0.0,  10.0),
}

# Median of compute_bsk_map across the live pool — used by _is_metronome
# for the Open tier so the rule stays viable across its full BSK span.
# (Math midpoint of [0..10] = 5.0, which sits above p98 of the pool.)
BSK_POOL_MEDIAN: float = 2.10


# ── Per-tier zone thresholds (Mod / Metronome / Pass / SS) ────────────────
# Anchored to the actual eligible-map mass inside each tier, NOT to (lo, hi)
# midpoints. A's tier range stretches to 10.0 but real A-maps cluster at
# [2.65, 4.29] (p66..p95) — a relative `bsk < lo + 0.25·(hi-lo)` rule would
# put the Mod zone at bsk<4.49 and cover ~80% of A's actual mass. These
# anchors come from the May 2026 live-pool dry-run and should be revisited
# whenever the pool composition shifts.
#
# Each tier has:
#   mod_top   — bsk strictly below → Mod (easy for tier)
#   met_mid   — |bsk - this| ≤ MET_WINDOW → Metronome
#   pass_bot  — bsk at-or-above   → Pass / SS (acc-axis gate)
#
# Open uses pool-wide percentiles (p25/p50/p75) so its zones span the full
# pool instead of the per-tier slice.
TIER_ZONES: dict[str, dict[str, float]] = {
    "C":    {"mod_top": 0.85, "met_mid": 1.10, "pass_bot": 1.45},
    "B":    {"mod_top": 1.95, "met_mid": 2.18, "pass_bot": 2.45},
    "A":    {"mod_top": 2.95, "met_mid": 3.30, "pass_bot": 3.70},
    "Open": {"mod_top": 1.10, "met_mid": 2.10, "pass_bot": 2.95},
}

# Half-width of the Metronome window around `met_mid`.
MET_WINDOW: float = 0.25


# ── BSK_map composite ──────────────────────────────────────────────────────

def compute_bsk_map(map_row: Any) -> float:
    """Σ w_axis · axis_stars; fallback to star_rating if axes are NULL.

    Mirrors services.hps.payout._map_info_for_bounty for the weights default
    of 0.25 each. Accepts duck-typed rows (anything with .aim_stars etc.).
    """
    aim   = getattr(map_row, "aim_stars",   None)
    speed = getattr(map_row, "speed_stars", None)
    acc   = getattr(map_row, "acc_stars",   None)
    cons  = getattr(map_row, "cons_stars",  None)

    if any(v is None for v in (aim, speed, acc, cons)):
        sr = float(getattr(map_row, "star_rating", 0.0) or 0.0)
        return sr

    w_aim   = float(getattr(map_row, "w_aim",   None) or 0.25)
    w_speed = float(getattr(map_row, "w_speed", None) or 0.25)
    w_acc   = float(getattr(map_row, "w_acc",   None) or 0.25)
    w_cons  = float(getattr(map_row, "w_cons",  None) or 0.25)
    return (
        w_aim   * float(aim)
        + w_speed * float(speed)
        + w_acc   * float(acc)
        + w_cons  * float(cons)
    )


# ── Pool selection ─────────────────────────────────────────────────────────

# ── Bounty-type caps for pick_for_tier ─────────────────────────────────────
# Soft caps applied during Phase 2 (random fill). A type already at its cap
# gets skipped over instead of pushing out variety. Phase 1 (guarantee one
# per present type) ignores these caps so SS/Marathon/Pass always get their
# first slot when eligible.
MAX_PER_TYPE: dict[str, int] = {
    "Marathon":  2,
    "SS":        1,
    "Accuracy":  3,
    "Metronome": 3,
    "Mod":       2,
    # Pass is a "carrot" rare type by mass but the highest-bsk slice of a tier
    # can sometimes be dominated by it — cap kept generous so it can fill gaps
    # when other types are scarce.
    "Pass":      4,
    "First FC":  3,
}

# Order in which types claim their guaranteed Phase 1 slot. Rare/featured
# types first so they don't get crowded out when the eligible pool has very
# few of them.
TYPE_PRIORITY: tuple[str, ...] = (
    "Marathon", "SS", "Pass", "Metronome", "Mod", "Accuracy", "First FC",
)


def pick_for_tier(maps: list[Any], tier: str, n: int = 9) -> list[Any]:
    """Select up to `n` maps for the tier's weekly pool.

    Two-phase stratification by bounty_type (not axis — bounty_type is what
    the player actually sees). Without per-type caps, A-tier on the live
    pool was 80% Mod because nearly all A-eligible maps fall into the Mod
    zone.

    Phase 1: ≥1 map of every bounty_type present in the eligible set.
    Phase 2: random fill of the remaining slots, skipping any type already
             at MAX_PER_TYPE for this run.

    Small pools (≤n eligible) are returned in closest-to-midpoint order with
    no random component — keeps `pick_for_tier` deterministic when the slice
    is fully constrained.
    """
    if tier not in TIER_BSK_RANGES:
        raise ValueError(f"unknown tier {tier!r}")
    lo, hi = TIER_BSK_RANGES[tier]
    mid = (lo + hi) / 2.0

    filtered = [m for m in maps if lo <= compute_bsk_map(m) < hi]
    filtered.sort(key=lambda m: abs(compute_bsk_map(m) - mid))
    if len(filtered) <= n:
        return filtered

    # Precompute bounty_type per map (assign_bounty_type is pure, no IO).
    typed: list[tuple[str, Any]] = [
        (assign_bounty_type(m, tier)[0], m) for m in filtered
    ]
    by_type: dict[str, list[Any]] = {}
    for bt, m in typed:
        by_type.setdefault(bt, []).append(m)

    picks: list[Any] = []
    picked_ids: set[int] = set()
    counts: dict[str, int] = {}

    def _take(bt: str, m: Any) -> None:
        picks.append(m)
        picked_ids.add(id(m))
        counts[bt] = counts.get(bt, 0) + 1

    # Phase 1 — one of every type present (priority order). Bypasses the cap.
    for bt in TYPE_PRIORITY:
        if len(picks) >= n:
            break
        bucket = by_type.get(bt)
        if bucket:
            _take(bt, random.choice(bucket))

    # Phase 2 — random fill respecting MAX_PER_TYPE strictly. If the pool has
    # genuine variety this is the only loop that runs and the cap holds.
    remaining = [(bt, m) for bt, m in typed if id(m) not in picked_ids]
    random.shuffle(remaining)

    for bt, m in remaining:
        if len(picks) >= n:
            break
        if counts.get(bt, 0) >= MAX_PER_TYPE.get(bt, n):
            continue
        _take(bt, m)

    # Phase 3 — emergency top-up. Cap-bypass is unavoidable here but we want
    # to drift back toward variety: each iteration picks the next leftover
    # map of whichever type currently has the LOWEST count. Decision is
    # re-made every iteration so a type going from count=2 to count=3 yields
    # the next slot to another type at count=2.
    if len(picks) < n:
        # Group leftover by type so the inner loop pops cheaply.
        leftover_by_type: dict[str, list[Any]] = {}
        for bt, m in remaining:
            if id(m) in picked_ids:
                continue
            leftover_by_type.setdefault(bt, []).append(m)

        while len(picks) < n:
            # Type with the smallest current count AND non-empty leftover.
            candidates = [bt for bt, lst in leftover_by_type.items() if lst]
            if not candidates:
                break
            best = min(candidates, key=lambda bt: counts.get(bt, 0))
            _take(best, leftover_by_type[best].pop())

    return picks


# ── Bounty-type rules ──────────────────────────────────────────────────────
# Each rule = (name, predicate, conditions_producer). Evaluated in order,
# first match wins. Conditions producers may consult the tier (for Mod
# rotation, etc.).

@dataclass(frozen=True)
class Rule:
    name: str
    predicate: Callable[[Any, str], bool]
    conditions: Callable[[Any, str], dict]


def _axis_max(map_row: Any) -> str | None:
    """Return the axis name with the highest stars, or None if any is NULL."""
    pairs = [
        ("aim",   getattr(map_row, "aim_stars",   None)),
        ("speed", getattr(map_row, "speed_stars", None)),
        ("acc",   getattr(map_row, "acc_stars",   None)),
        ("cons",  getattr(map_row, "cons_stars",  None)),
    ]
    if any(v is None for _, v in pairs):
        return None
    return max(pairs, key=lambda p: p[1])[0]


def _is_marathon(map_row: Any, _tier: str) -> bool:
    # length is in seconds in BskMapPool. Rare special by design — only 0.3%
    # of the live pool qualifies, but the stratified picker (`pick_for_tier`)
    # gives Marathons priority placement when any are eligible.
    length = getattr(map_row, "length", None) or getattr(map_row, "drain_time", None) or 0
    return length >= 600  # 10 minutes


def _is_ss(map_row: Any, tier: str) -> bool:
    # Acc-dominant AND inside the tier's Pass zone (hardest band of the tier).
    # Reuses TIER_ZONES.pass_bot so SS and Pass share one anchor — moves
    # together when the pool is recalibrated.
    if _axis_max(map_row) != "acc":
        return False
    return compute_bsk_map(map_row) >= TIER_ZONES[tier]["pass_bot"]


def _is_accuracy(map_row: Any, _tier: str) -> bool:
    return _axis_max(map_row) == "acc"


def _is_metronome(map_row: Any, tier: str) -> bool:
    # Met-mid is per-tier (TIER_ZONES.met_mid). Open uses pool-wide p50.
    mid = TIER_ZONES[tier]["met_mid"]
    return abs(compute_bsk_map(map_row) - mid) <= MET_WINDOW


def _is_mod_easy(map_row: Any, tier: str) -> bool:
    # Easy slice of the tier's actual mass — bsk < tier-specific mod_top.
    return compute_bsk_map(map_row) < TIER_ZONES[tier]["mod_top"]


def _is_pass_hard(map_row: Any, tier: str) -> bool:
    # Hard slice of the tier's actual mass — bsk >= tier-specific pass_bot.
    return compute_bsk_map(map_row) >= TIER_ZONES[tier]["pass_bot"]


_MOD_ROTATION = ("HR", "HD", "DT")


def _mod_for_map(map_row: Any) -> str:
    """Pick a mod deterministically from beatmap_id so each weekly run is
    reproducible. Open maps cycle through HR/HD/DT independent of tier."""
    bid = int(getattr(map_row, "beatmap_id", 0) or 0)
    return _MOD_ROTATION[bid % len(_MOD_ROTATION)]


BOUNTY_TYPE_RULES: list[Rule] = [
    Rule("Marathon", _is_marathon,
         lambda _m, _t: {"min_combo_pct": 0.8}),
    Rule("SS",        _is_ss,
         lambda _m, _t: {"min_accuracy": 100.0}),
    Rule("Accuracy",  _is_accuracy,
         lambda _m, _t: {"min_accuracy": 98.5}),
    Rule("Metronome", _is_metronome,
         lambda _m, _t: {"max_ur": 75}),
    Rule("Mod",       _is_mod_easy,
         lambda m, _t: {"required_mods": [_mod_for_map(m)]}),
    Rule("Pass",      _is_pass_hard,
         lambda _m, _t: {}),
]


def assign_bounty_type(map_row: Any, tier: str) -> tuple[str, dict]:
    """Apply BOUNTY_TYPE_RULES in order. Falls back to ('First FC', {})."""
    for rule in BOUNTY_TYPE_RULES:
        if rule.predicate(map_row, tier):
            return rule.name, rule.conditions(map_row, tier)
    return "First FC", {}
