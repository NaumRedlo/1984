"""Unit tests for services.bounty.tier_rules.

Plan: unified-giggling-tiger.

Covers:
  * get_tier_for_hp thresholds via existing RANK_THRESHOLDS.
  * pick_for_tier filtering by BSK_map range.
  * assign_bounty_type rule order (Marathon → SS → Accuracy → Metronome → Mod → Pass → fallback).
  * conditions JSON round-trip.

All tests use a minimal MapStub instead of BskMapPool so they have zero DB
deps. Anything tier_rules.compute_bsk_map reads with getattr is satisfied.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from services.bounty.tier_rules import (
    BOUNTY_TYPE_RULES,
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
    aim_stars: float = 5.0
    speed_stars: float = 5.0
    acc_stars: float = 5.0
    cons_stars: float = 5.0
    w_aim: float = 0.25
    w_speed: float = 0.25
    w_acc: float = 0.25
    w_cons: float = 0.25
    star_rating: float = 5.0
    length: int = 180


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
        m = MapStub(aim_stars=6, speed_stars=6, acc_stars=6, cons_stars=6)
        assert compute_bsk_map(m) == pytest.approx(6.0)

    def test_weighted(self):
        m = MapStub(
            aim_stars=8, speed_stars=2, acc_stars=2, cons_stars=2,
            w_aim=0.7, w_speed=0.1, w_acc=0.1, w_cons=0.1,
        )
        # 0.7*8 + 0.1*2*3 = 5.6 + 0.6 = 6.2
        assert compute_bsk_map(m) == pytest.approx(6.2)

    def test_fallback_to_sr_when_axes_missing(self):
        m = MapStub(aim_stars=None, star_rating=7.3)
        assert compute_bsk_map(m) == pytest.approx(7.3)


# ── pick_for_tier ───────────────────────────────────────────────────────────

class TestPickForTier:
    def _spread(self, bsks: list[float]) -> list[MapStub]:
        return [
            MapStub(beatmap_id=i, aim_stars=b, speed_stars=b, acc_stars=b, cons_stars=b)
            for i, b in enumerate(bsks)
        ]

    def test_returns_at_most_n_maps(self):
        maps = self._spread([3.0] * 12)  # all in tier C
        picks = pick_for_tier(maps, "C", n=9)
        assert len(picks) == 9

    def test_filters_to_tier_range(self):
        maps = self._spread([1.0, 2.0, 3.0, 4.4, 5.0, 5.5, 6.0, 7.0, 8.0, 9.0])
        c = pick_for_tier(maps, "C", n=99)
        b = pick_for_tier(maps, "B", n=99)
        a = pick_for_tier(maps, "A", n=99)
        # C range = [0.0, 4.5)
        assert {compute_bsk_map(m) for m in c} == {1.0, 2.0, 3.0, 4.4}
        # B range = [4.5, 6.5)
        assert {compute_bsk_map(m) for m in b} == {5.0, 5.5, 6.0}
        # A range = [6.5, 10.0)
        assert {compute_bsk_map(m) for m in a} == {7.0, 8.0, 9.0}

    def test_open_includes_everything(self):
        maps = self._spread([0.5, 5.0, 9.5])
        assert len(pick_for_tier(maps, "Open", n=99)) == 3

    def test_unknown_tier_raises(self):
        with pytest.raises(ValueError):
            pick_for_tier([], "Z")

    def test_empty_pool_returns_empty(self):
        assert pick_for_tier([], "B") == []

    def test_sort_by_distance_from_midpoint(self):
        # B midpoint = 5.5. Map with bsk=5.5 should come before 4.6.
        maps = [
            MapStub(beatmap_id=1, aim_stars=4.6, speed_stars=4.6, acc_stars=4.6, cons_stars=4.6),
            MapStub(beatmap_id=2, aim_stars=5.5, speed_stars=5.5, acc_stars=5.5, cons_stars=5.5),
            MapStub(beatmap_id=3, aim_stars=6.4, speed_stars=6.4, acc_stars=6.4, cons_stars=6.4),
        ]
        picks = pick_for_tier(maps, "B", n=3)
        assert picks[0].beatmap_id == 2  # exact midpoint wins


# ── assign_bounty_type ──────────────────────────────────────────────────────

class TestAssignBountyType:
    def test_marathon_threshold_600(self):
        below = MapStub(length=599)
        above = MapStub(length=600)
        assert assign_bounty_type(below, "B")[0] != "Marathon"
        assert assign_bounty_type(above, "B") == ("Marathon", {"min_combo_pct": 0.8})

    def test_ss_requires_acc_max_and_acc_8plus(self):
        m = MapStub(aim_stars=4, speed_stars=4, acc_stars=9, cons_stars=4)
        bt, cond = assign_bounty_type(m, "A")
        assert bt == "SS"
        assert cond == {"min_accuracy": 100.0}

    def test_accuracy_when_acc_max_but_below_8(self):
        m = MapStub(aim_stars=4, speed_stars=4, acc_stars=5, cons_stars=4)
        bt, cond = assign_bounty_type(m, "B")
        assert bt == "Accuracy"
        assert cond == {"min_accuracy": 98.5}

    def test_pass_carrot_at_top_of_tier(self):
        # B range top = 6.5 (exclusive); 6.4 satisfies bsk >= hi - 1.0 = 5.5.
        # Need a map that doesn't trip Metronome (|bsk - mid|<=0.5, mid=5.5):
        # bsk in [6.0, 6.5) lies above metronome window AND inside pass window.
        m = MapStub(aim_stars=6.2, speed_stars=6.2, acc_stars=6.2, cons_stars=6.2)
        bt, cond = assign_bounty_type(m, "B")
        assert bt == "Pass"
        assert cond == {}

    def test_mod_at_bottom_of_tier(self):
        # B range bottom = 4.5; bsk just above bottom triggers Mod (< lo + 1.0).
        # Need to avoid axes triggering Accuracy: keep all equal.
        m = MapStub(beatmap_id=2, aim_stars=4.6, speed_stars=4.6, acc_stars=4.6, cons_stars=4.6)
        bt, cond = assign_bounty_type(m, "B")
        assert bt == "Mod"
        assert "required_mods" in cond
        assert cond["required_mods"][0] in ("HR", "HD", "DT")

    def test_metronome_middle_of_tier(self):
        # Tier B midpoint 5.5; bsk=5.5 triggers Metronome before Pass/Mod.
        m = MapStub(aim_stars=5.5, speed_stars=5.5, acc_stars=5.5, cons_stars=5.5)
        bt, cond = assign_bounty_type(m, "B")
        assert bt == "Metronome"
        assert cond == {"max_ur": 75}

    def test_fallback_first_fc(self):
        # Fallback fires when ALL rules miss:
        #   not marathon (length<600), not acc-max axis, not metronome
        #   (|bsk - midpoint| > 0.5), not Mod (bsk >= lo+1.0),
        #   not Pass (bsk < hi-1.0).
        # A-tier: lo=6.5, hi=10.0, midpoint=8.25.
        #   Mod zone:        [6.5, 7.5)
        #   Metronome zone:  [7.75, 8.75]
        #   Pass zone:       [9.0, 10.0)
        # The gap [7.5, 7.75) falls through to "First FC". Pick bsk=7.6:
        m = MapStub(
            beatmap_id=42,
            aim_stars=7.4, speed_stars=8.2, acc_stars=7.4, cons_stars=7.4,
        )
        # bsk = 0.25*(7.4 + 8.2 + 7.4 + 7.4) = 7.6
        assert compute_bsk_map(m) == pytest.approx(7.6)
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
