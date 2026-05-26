"""Unit tests for services.bounty.tier_rules.

Plan: unified-giggling-tiger.

Covers:
  * get_tier_for_hp thresholds via existing RANK_THRESHOLDS.
  * pick_for_tier filtering by BSK_map range + axis stratification.
  * assign_bounty_type rule order (Marathon → SS → Accuracy → Metronome → Mod → Pass → fallback).
  * conditions JSON round-trip.

The tier ranges were recalibrated in May 2026 from the live pool audit:
  C    = [0.0,  1.70)   ≈ p0..p40
  B    = [1.70, 2.65)   ≈ p40..p66
  A    = [2.65, 10.0)   ≈ p66..top
  Open = [0.0,  10.0)

All tests use a minimal MapStub instead of BskMapPool so they have zero DB
deps. Anything tier_rules.compute_bsk_map reads with getattr is satisfied.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass

import pytest

from services.bounty.tier_rules import (
    BOUNTY_TYPE_RULES,
    BSK_POOL_MEDIAN,
    TIER_BSK_RANGES,
    assign_bounty_type,
    compute_bsk_map,
    pick_for_tier,
)
from utils.hp_calculator import get_tier_for_hp


# ── Minimal map row ─────────────────────────────────────────────────────────

@dataclass
class MapStub:
    beatmap_id: int = 0
    aim_stars: float = 2.0
    speed_stars: float = 2.0
    acc_stars: float = 2.0
    cons_stars: float = 2.0
    w_aim: float = 0.25
    w_speed: float = 0.25
    w_acc: float = 0.25
    w_cons: float = 0.25
    star_rating: float = 4.0
    length: int = 180


def _flat(beatmap_id: int, bsk: float, length: int = 180) -> MapStub:
    """Helper: a map with all four axes equal to `bsk` (so compute_bsk_map==bsk)."""
    return MapStub(
        beatmap_id=beatmap_id,
        aim_stars=bsk, speed_stars=bsk, acc_stars=bsk, cons_stars=bsk,
        length=length,
    )


# ── get_tier_for_hp ─────────────────────────────────────────────────────────

class TestGetTierForHp:
    @pytest.mark.parametrize("hp,expected", [
        (0, "C"),
        (249, "C"),    # top of Candidate
        (250, "C"),    # bottom of Member
        (749, "C"),    # top of Member
        (750, "B"),    # bottom of Inspector
        (1499, "B"),   # top of Inspector
        (1500, "A"),   # bottom of Commissioner
        (2999, "A"),   # top of Commissioner
        (3000, "A"),   # bottom of Big Brother
        (10000, "A"),
    ])
    def test_thresholds(self, hp, expected):
        assert get_tier_for_hp(hp) == expected

    def test_uses_v2_ranks(self):
        from utils.hp_calculator import get_rank_for_hp
        # If anyone changes the rank lookup, this test will surface it.
        assert get_rank_for_hp(2500) == "Commissioner"
        assert get_tier_for_hp(2500) == "A"


# ── compute_bsk_map ─────────────────────────────────────────────────────────

class TestComputeBskMap:
    def test_equal_axes_equal_weights(self):
        m = MapStub(aim_stars=3, speed_stars=3, acc_stars=3, cons_stars=3)
        assert compute_bsk_map(m) == pytest.approx(3.0)

    def test_weighted(self):
        m = MapStub(
            aim_stars=8, speed_stars=2, acc_stars=2, cons_stars=2,
            w_aim=0.7, w_speed=0.1, w_acc=0.1, w_cons=0.1,
        )
        # 0.7*8 + 0.1*2*3 = 5.6 + 0.6 = 6.2
        assert compute_bsk_map(m) == pytest.approx(6.2)

    def test_fallback_to_sr_when_axes_missing(self):
        m = MapStub(aim_stars=None, star_rating=4.7)
        assert compute_bsk_map(m) == pytest.approx(4.7)


# ── pick_for_tier ───────────────────────────────────────────────────────────

class TestPickForTier:
    def test_returns_at_most_n_maps(self):
        # 12 maps all in C-range (bsk=1.0) — picker must cap at 9.
        maps = [_flat(i, 1.0) for i in range(12)]
        picks = pick_for_tier(maps, "C", n=9)
        assert len(picks) == 9

    def test_filters_to_tier_range(self):
        # Maps spread across all ranges: 0.5, 1.0, 1.6 (C), 1.7, 2.0, 2.6 (B), 2.7, 5.0, 9.0 (A)
        maps = [_flat(i, b) for i, b in enumerate([0.5, 1.0, 1.6, 1.7, 2.0, 2.6, 2.7, 5.0, 9.0])]
        c = pick_for_tier(maps, "C", n=99)
        b = pick_for_tier(maps, "B", n=99)
        a = pick_for_tier(maps, "A", n=99)
        # C range = [0.0, 1.70)
        assert {round(compute_bsk_map(m), 2) for m in c} == {0.5, 1.0, 1.6}
        # B range = [1.70, 2.65)
        assert {round(compute_bsk_map(m), 2) for m in b} == {1.7, 2.0, 2.6}
        # A range = [2.65, 10.0)
        assert {round(compute_bsk_map(m), 2) for m in a} == {2.7, 5.0, 9.0}

    def test_open_includes_everything(self):
        maps = [_flat(i, b) for i, b in enumerate([0.5, 2.0, 9.5])]
        assert len(pick_for_tier(maps, "Open", n=99)) == 3

    def test_unknown_tier_raises(self):
        with pytest.raises(ValueError):
            pick_for_tier([], "Z")

    def test_empty_pool_returns_empty(self):
        assert pick_for_tier([], "B") == []

    def test_small_pool_sorted_by_midpoint(self):
        # B midpoint = (1.70+2.65)/2 ≈ 2.175. With ≤n maps, picker returns
        # everyone sorted by distance from midpoint — the closest first.
        maps = [
            _flat(1, 1.75),  # |1.75 - 2.175| = 0.425
            _flat(2, 2.17),  # |2.17 - 2.175| ≈ 0.005 — closest
            _flat(3, 2.60),  # |2.60 - 2.175| = 0.425
        ]
        picks = pick_for_tier(maps, "B", n=9)
        assert picks[0].beatmap_id == 2

    def test_stratifies_by_axis(self):
        # 1 speed-dominant map + 20 aim-dominant maps in B-range. Stratified
        # picker MUST include the speed map even though random.sample would
        # almost never pick it (1/21).
        random.seed(0)
        speed_map = MapStub(
            beatmap_id=999,
            aim_stars=1.5, speed_stars=4.0, acc_stars=1.0, cons_stars=1.5,
            length=180,
        )
        # bsk = (1.5+4+1+1.5)/4 = 2.0  (inside B-range)
        assert 1.70 <= compute_bsk_map(speed_map) < 2.65

        aim_maps = [
            MapStub(
                beatmap_id=i,
                aim_stars=4.0, speed_stars=1.5, acc_stars=1.0, cons_stars=1.5,
                length=180,
            )
            for i in range(20)
        ]
        picks = pick_for_tier([speed_map, *aim_maps], "B", n=9)
        assert speed_map in picks

    def test_stratifies_each_axis_when_all_present(self):
        # One of each axis-dominant flavour + filler. Picker must include
        # all four axis representatives in its 9.
        random.seed(1)
        axes = ["aim", "speed", "acc", "cons"]
        # Per-axis prototype inside B-range (bsk ≈ 2.0).
        proto = {
            "aim":   (4.0, 1.5, 1.0, 1.5),
            "speed": (1.5, 4.0, 1.0, 1.5),
            "acc":   (1.5, 1.0, 4.0, 1.5),
            "cons":  (1.5, 1.0, 1.5, 4.0),
        }
        named: dict[str, MapStub] = {}
        all_maps: list[MapStub] = []
        for i, axis in enumerate(axes):
            a, s, ac, c = proto[axis]
            m = MapStub(beatmap_id=100 + i,
                        aim_stars=a, speed_stars=s, acc_stars=ac, cons_stars=c,
                        length=180)
            named[axis] = m
            all_maps.append(m)
        # 20 filler aim-maps to push the eligible count > 9.
        for j in range(20):
            all_maps.append(MapStub(
                beatmap_id=200 + j,
                aim_stars=4.0, speed_stars=1.5, acc_stars=1.0, cons_stars=1.5,
                length=180,
            ))
        picks = pick_for_tier(all_maps, "B", n=9)
        for axis in axes:
            assert named[axis] in picks, f"missing {axis} representative"


# ── assign_bounty_type ──────────────────────────────────────────────────────

class TestAssignBountyType:
    def test_marathon_threshold_600(self):
        below = MapStub(length=599)
        above = MapStub(length=600)
        assert assign_bounty_type(below, "B")[0] != "Marathon"
        assert assign_bounty_type(above, "B") == ("Marathon", {"min_combo_pct": 0.8})

    def test_ss_relative_to_tier(self):
        # SS = acc-dominant AND bsk >= lo + 0.7*(hi-lo).
        # A-tier: lo=2.65, hi=10.0 → threshold = 2.65 + 0.7*7.35 = 7.795.
        # Map at A-tier bottom + acc-max: NOT SS.
        low_acc = MapStub(
            beatmap_id=1,
            aim_stars=1.0, speed_stars=1.0, acc_stars=2.7, cons_stars=1.0,
        )
        # bsk = (1+1+2.7+1)/4 = 1.425 (NOT in A — would route to C). Construct
        # an A-bottom map differently: keep average ≥2.65 but acc still max.
        a_bottom = MapStub(
            beatmap_id=2,
            aim_stars=2.8, speed_stars=2.6, acc_stars=2.9, cons_stars=2.6,
        )
        # bsk = (2.8+2.6+2.9+2.6)/4 = 2.725 — inside A but below SS threshold 7.795
        assert assign_bounty_type(a_bottom, "A")[0] != "SS"

        # High-acc-dominant map: bsk >= 7.795.
        a_high = MapStub(
            beatmap_id=3,
            aim_stars=7.5, speed_stars=7.5, acc_stars=8.5, cons_stars=7.5,
        )
        # bsk = (7.5+7.5+8.5+7.5)/4 = 7.75 — just BELOW threshold; bump it up.
        a_higher = MapStub(
            beatmap_id=4,
            aim_stars=7.7, speed_stars=7.7, acc_stars=9.0, cons_stars=7.7,
        )
        # bsk = (7.7+7.7+9.0+7.7)/4 = 8.025 — above threshold AND acc=max
        assert compute_bsk_map(a_higher) >= 2.65 + 0.7 * (10.0 - 2.65)
        bt, cond = assign_bounty_type(a_higher, "A")
        assert bt == "SS"
        assert cond == {"min_accuracy": 100.0}

    def test_accuracy_when_acc_max_but_below_ss_threshold(self):
        # B-tier mid, acc-axis-max but bsk well below B's SS threshold
        # (1.70 + 0.7*0.95 ≈ 2.365). bsk=2.0 < 2.365 → Accuracy, not SS.
        m = MapStub(aim_stars=1.7, speed_stars=1.7, acc_stars=2.9, cons_stars=1.7)
        # bsk = (1.7+1.7+2.9+1.7)/4 = 2.0
        assert compute_bsk_map(m) == pytest.approx(2.0)
        bt, cond = assign_bounty_type(m, "B")
        assert bt == "Accuracy"
        assert cond == {"min_accuracy": 98.5}

    def test_metronome_middle_of_tier(self):
        # B midpoint = (1.70+2.65)/2 = 2.175; window ±0.25. Map with all axes
        # equal to 2.175 is exactly mid AND has no axis-max so it skips Acc.
        m = _flat(1, 2.175)
        bt, cond = assign_bounty_type(m, "B")
        assert bt == "Metronome"
        assert cond == {"max_ur": 75}

    def test_metronome_open_uses_pool_median(self):
        # Open midpoint should be the pool median (≈2.10), not (lo+hi)/2 = 5.0.
        m = _flat(1, BSK_POOL_MEDIAN)
        bt, _ = assign_bounty_type(m, "Open")
        assert bt == "Metronome"

    def test_metronome_open_does_not_match_math_midpoint(self):
        # Sanity: bsk=5.0 (old math midpoint) is FAR from the pool median →
        # must NOT be Metronome on Open under the new rule.
        m = _flat(1, 5.0)
        bt, _ = assign_bounty_type(m, "Open")
        assert bt != "Metronome"

    def test_mod_at_bottom_of_tier(self):
        # B bottom 25% = bsk < 1.70 + 0.25*0.95 = 1.9375. Use bsk=1.80 inside
        # B-range, all axes equal to skip Accuracy.
        m = _flat(2, 1.80)
        bt, cond = assign_bounty_type(m, "B")
        assert bt == "Mod"
        assert "required_mods" in cond
        assert cond["required_mods"][0] in ("HR", "HD", "DT")

    def test_pass_carrot_at_top_of_tier(self):
        # B top 25% = bsk >= 1.70 + 0.75*0.95 = 2.4125. Use bsk=2.50 inside
        # B-range, all axes equal to skip Accuracy. Mid = 2.175 so |2.5 - 2.175| =
        # 0.325 > 0.25 → not Metronome.
        m = _flat(3, 2.50)
        bt, cond = assign_bounty_type(m, "B")
        assert bt == "Pass"
        assert cond == {}

    def test_fallback_first_fc(self):
        # Fallback fires when NO rule matches:
        #   length < 600
        #   not acc-axis-max
        #   |bsk - tier_mid| > 0.25
        #   bsk not in bottom-25% (Mod zone)
        #   bsk not in top-25% (Pass zone)
        # B-tier zones: Mod < 1.9375; Metronome [1.925, 2.425]; Pass >= 2.4125.
        # The gap [1.9375, 1.925) is empty by these numbers — try A-tier:
        # A: lo=2.65, hi=10.0, mid=6.325.
        #   Mod zone:     bsk < 2.65 + 0.25*7.35 = 4.4875
        #   Metronome:    |bsk - 6.325| <= 0.25  → [6.075, 6.575]
        #   Pass zone:    bsk >= 2.65 + 0.75*7.35 = 8.1625
        # A gap [4.4875, 6.075) and [6.575, 8.1625) → use bsk≈5.0 with no
        # acc dominance.
        m = MapStub(
            beatmap_id=42,
            aim_stars=5.5, speed_stars=5.0, acc_stars=4.5, cons_stars=5.0,
        )
        # bsk = (5.5+5+4.5+5)/4 = 5.0 — A-range, in gap, aim is max (not acc).
        assert compute_bsk_map(m) == pytest.approx(5.0)
        bt, cond = assign_bounty_type(m, "A")
        assert bt == "First FC"
        assert cond == {}


# ── conditions round-trip ───────────────────────────────────────────────────

class TestConditionsRoundtrip:
    def test_json_roundtrip_all_rule_outputs(self):
        # For every rule, produce its conditions and ensure JSON survives.
        m = MapStub(length=700)  # marathon
        for rule in BOUNTY_TYPE_RULES:
            cond = rule.conditions(m, "B")
            roundtripped = json.loads(json.dumps(cond, ensure_ascii=False))
            assert roundtripped == cond


# ── TIER_BSK_RANGES invariants ──────────────────────────────────────────────

class TestTierBskRanges:
    def test_open_spans_full_range(self):
        lo, hi = TIER_BSK_RANGES["Open"]
        assert lo == 0.0
        assert hi >= 10.0

    def test_c_b_a_partition_is_contiguous(self):
        c_lo, c_hi = TIER_BSK_RANGES["C"]
        b_lo, b_hi = TIER_BSK_RANGES["B"]
        a_lo, _   = TIER_BSK_RANGES["A"]
        assert c_hi == b_lo  # no gap between C and B
        assert b_hi == a_lo  # no gap between B and A

    def test_bsk_pool_median_constant_in_open_range(self):
        lo, hi = TIER_BSK_RANGES["Open"]
        assert lo <= BSK_POOL_MEDIAN < hi
