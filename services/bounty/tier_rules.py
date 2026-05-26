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
# Theoretical defaults — refine after the first weekly dry-run on real data.
# C+M players (HPS rank Candidate/Member) typically aim for maps in
# the lower BSK band; Big Brother for the upper. Open is a free-for-all.
TIER_BSK_RANGES: dict[str, tuple[float, float]] = {
    "C":    (0.0, 4.5),
    "B":    (4.5, 6.5),
    "A":    (6.5, 10.0),
    "Open": (0.0, 10.0),
}


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

    Randomised: shuffles the eligible pool so each weekly generation picks
    different maps. Uses random.sample when pool is large enough, otherwise
    returns all eligible maps.
    """
    if tier not in TIER_BSK_RANGES:
        raise ValueError(f"unknown tier {tier!r}")
    lo, hi = TIER_BSK_RANGES[tier]

    filtered = [m for m in maps if lo <= compute_bsk_map(m) < hi]
    if len(filtered) <= n:
        return filtered
    return random.sample(filtered, n)


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
    # length is in seconds in BskMapPool.
    length = getattr(map_row, "length", None) or getattr(map_row, "drain_time", None) or 0
    return length >= 600  # 10 minutes


def _is_ss(map_row: Any, _tier: str) -> bool:
    if _axis_max(map_row) != "acc":
        return False
    acc = getattr(map_row, "acc_stars", None)
    return acc is not None and acc >= 8.0


def _is_accuracy(map_row: Any, _tier: str) -> bool:
    return _axis_max(map_row) == "acc"


def _is_metronome(map_row: Any, tier: str) -> bool:
    lo, hi = TIER_BSK_RANGES[tier]
    mid = (lo + hi) / 2.0
    bsk = compute_bsk_map(map_row)
    return abs(bsk - mid) <= 0.5


def _is_mod_easy(map_row: Any, tier: str) -> bool:
    lo, _hi = TIER_BSK_RANGES[tier]
    return compute_bsk_map(map_row) < lo + 1.0


def _is_pass_hard(map_row: Any, tier: str) -> bool:
    _lo, hi = TIER_BSK_RANGES[tier]
    return compute_bsk_map(map_row) >= hi - 1.0


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
