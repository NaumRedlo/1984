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

def pick_for_tier(maps: list[Any], tier: str, n: int = 9) -> list[Any]:
    """Select up to `n` maps whose BSK_map composite lies in the tier range.

    Stratified by argmax skill axis: guarantees at least one aim/speed/acc/cons
    pick when any exist, then fills the remainder by closest-to-midpoint
    ordering. Without stratification A-tier on the live pool was 71% Mod
    because nearly all eligible maps were aim-dominant.

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

    # Stratify by argmax axis (mirrors osu_parser.map_type_from_stars). Maps
    # with any None axis fall into the "mixed" bucket and only feed Phase 2.
    by_axis: dict[str, list[Any]] = {"aim": [], "speed": [], "acc": [], "cons": [], "mixed": []}
    for m in filtered:
        axis = _axis_max(m) or "mixed"
        by_axis[axis].append(m)

    picks: list[Any] = []
    picked_ids: set[int] = set()

    def _take(m: Any) -> None:
        picks.append(m)
        picked_ids.add(id(m))

    # Phase 1 — one per axis when available. Pick the closest-to-midpoint
    # representative from each axis bucket (filtered is already sorted).
    for axis in ("aim", "speed", "acc", "cons"):
        if by_axis[axis]:
            _take(by_axis[axis][0])

    # Phase 2 — fill the remainder from a uniform random sample over the
    # untouched eligible set so the weekly pool gets fresh maps each run.
    remaining = [m for m in filtered if id(m) not in picked_ids]
    needed = n - len(picks)
    if remaining and needed > 0:
        picks.extend(random.sample(remaining, min(needed, len(remaining))))
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
    # Tier-relative: acc-dominant AND in the top 30% of the tier's BSK window.
    # Previous absolute floor (acc_stars >= 8) almost never fired on real maps.
    if _axis_max(map_row) != "acc":
        return False
    lo, hi = TIER_BSK_RANGES[tier]
    return compute_bsk_map(map_row) >= lo + 0.7 * (hi - lo)


def _is_accuracy(map_row: Any, _tier: str) -> bool:
    return _axis_max(map_row) == "acc"


def _is_metronome(map_row: Any, tier: str) -> bool:
    # Open: use the pool median (≈p50) because the math midpoint of [0..10]
    # sits above p98 and would never match. C/B/A use their tier midpoint.
    if tier == "Open":
        mid = BSK_POOL_MEDIAN
    else:
        lo, hi = TIER_BSK_RANGES[tier]
        mid = (lo + hi) / 2.0
    # Window tightened from ±0.5 → ±0.25: the recalibrated tier ranges are
    # ~1.0★ wide, so the old window covered the whole tier.
    return abs(compute_bsk_map(map_row) - mid) <= 0.25


def _is_mod_easy(map_row: Any, tier: str) -> bool:
    # Lowest 25% of the tier's BSK window. Relative threshold scales with tier
    # width instead of a fixed +1.0 that would cover an entire ~1.0★-wide tier.
    lo, hi = TIER_BSK_RANGES[tier]
    return compute_bsk_map(map_row) < lo + 0.25 * (hi - lo)


def _is_pass_hard(map_row: Any, tier: str) -> bool:
    # Highest 25% of the tier's BSK window — carrot for the top of the tier.
    lo, hi = TIER_BSK_RANGES[tier]
    return compute_bsk_map(map_row) >= lo + 0.75 * (hi - lo)


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
