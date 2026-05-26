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
    MAX_PER_TYPE,
    TIER_BSK_RANGES,
    TIER_ZONES,
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

    def test_stratifies_rare_type(self):
        # 1 marathon-eligible map + 30 Mod-zone filler in B-range. Type
        # stratification MUST include the marathon (Phase 1 priority).
        random.seed(0)
        # Marathon: bsk inside B, length≥600. Mid of B is 2.18, mod_top=1.95.
        # Use bsk=2.30 (Metronome window is [1.93, 2.43] so this is Metronome
        # by zone — but Marathon predicate fires BEFORE Metronome).
        marathon = _flat(999, 2.30, length=700)
        # Mod-zone: bsk < 1.95.
        mods = [_flat(i, 1.50) for i in range(30)]
        picks = pick_for_tier([marathon, *mods], "B", n=9)
        assert marathon in picks

    def test_stratifies_each_type_when_all_present(self):
        # Construct one map of each bounty type in B-range. Picker must
        # include at least one of every type in its 9 picks.
        random.seed(1)
        zones = TIER_ZONES["B"]  # mod_top=1.95, met_mid=2.18, pass_bot=2.45
        # Marathon: bsk in B + length ≥ 600 (predicate order puts it first).
        marathon = _flat(1, 2.30, length=700)
        # SS: bsk >= pass_bot AND acc-axis-max.
        ss = MapStub(beatmap_id=2,
                     aim_stars=2.2, speed_stars=2.2, acc_stars=3.5, cons_stars=2.2,
                     length=180)  # bsk = (2.2+2.2+3.5+2.2)/4 = 2.525
        # Accuracy: acc-axis-max but BELOW pass_bot.
        accuracy = MapStub(beatmap_id=3,
                           aim_stars=1.8, speed_stars=1.8, acc_stars=2.6, cons_stars=1.8,
                           length=180)  # bsk = 2.0
        # Metronome: bsk close to met_mid, no axis-max dominance.
        metronome = _flat(4, 2.18)
        # Mod: bsk < mod_top.
        mod = _flat(5, 1.80)
        # Pass: bsk >= pass_bot, NOT acc-axis-max.
        pass_map = MapStub(beatmap_id=6,
                           aim_stars=3.5, speed_stars=2.2, acc_stars=2.2, cons_stars=2.2,
                           length=180)  # bsk = 2.525, aim-max
        # First FC: gap between zones.
        # B gap: bsk in [mod_top, met_mid - 0.25) = [1.95, 1.93) — empty.
        # OR  [met_mid + 0.25, pass_bot) = (2.43, 2.45) — also tiny.
        # For B we have to settle for ≤ 6 types; First FC genuinely rare here.
        all_maps = [marathon, ss, accuracy, metronome, mod, pass_map]
        # 20 mod-zone filler so eligible > n.
        all_maps.extend(_flat(100 + j, 1.50) for j in range(20))

        picks = pick_for_tier(all_maps, "B", n=9)
        types_present = {assign_bounty_type(m, "B")[0] for m in picks}
        # Every prototype's type must appear.
        for expected in ("Marathon", "SS", "Accuracy", "Metronome", "Mod", "Pass"):
            assert expected in types_present, f"missing {expected} in {types_present}"

    def test_caps_dominant_type(self):
        # 50 Mod-zone maps in A-tier, 0 of anything else. Without Phase 3
        # fallback the picker would short-pick at MAX_PER_TYPE["Mod"]=2.
        # Phase 3 must top up to n=9 ignoring caps.
        random.seed(2)
        # A zone: mod_top=2.95, so bsk=2.80 is Mod.
        mods = [_flat(i, 2.80) for i in range(50)]
        picks = pick_for_tier(mods, "A", n=9)
        assert len(picks) == 9
        # Soft cap is bypassed only when forced — but at least the cap was
        # respected during Phase 2, so the Phase 3 top-up is what filled it.
        # All picks share type Mod here because no other type exists.
        types = {assign_bounty_type(m, "A")[0] for m in picks}
        assert types == {"Mod"}

    def test_cap_softens_mod_dominance_when_alternatives_exist(self):
        # 50 Mod-zone maps + 20 Pass maps in A-tier. Without caps the result
        # would skew heavily Mod (50/70 of the pool). With strict-Phase-2
        # caps + variety-aware Phase 3 top-up, Mod ends up at most as common
        # as Pass — drift back toward parity.
        random.seed(3)
        mods = [_flat(i, 2.80) for i in range(50)]
        passes = [_flat(100 + i, 3.80) for i in range(20)]  # bsk≥3.70 → Pass
        picks = pick_for_tier([*mods, *passes], "A", n=9)
        counts: dict[str, int] = {}
        for m in picks:
            bt, _ = assign_bounty_type(m, "A")
            counts[bt] = counts.get(bt, 0) + 1
        # Both types represented.
        assert counts.get("Pass", 0) >= 1
        assert counts.get("Mod", 0) >= 1
        # Mod must not dwarf Pass — the picker must equalise within ±1.
        assert abs(counts.get("Mod", 0) - counts.get("Pass", 0)) <= 1, (
            f"Mod/Pass skew exceeds 1: {counts}"
        )


# ── assign_bounty_type ──────────────────────────────────────────────────────

class TestAssignBountyType:
    def test_marathon_threshold_600(self):
        below = MapStub(length=599)
        above = MapStub(length=600)
        assert assign_bounty_type(below, "B")[0] != "Marathon"
        assert assign_bounty_type(above, "B") == ("Marathon", {"min_combo_pct": 0.8})

    def test_ss_anchored_to_pass_bot(self):
        # SS fires when acc-axis-max AND bsk >= TIER_ZONES[tier]["pass_bot"].
        # A-tier: pass_bot=3.70.
        # Map at A-tier bottom + acc-max but bsk < 3.70: NOT SS (falls to Accuracy).
        a_bottom = MapStub(
            beatmap_id=2,
            aim_stars=2.8, speed_stars=2.6, acc_stars=2.9, cons_stars=2.6,
        )
        # bsk = (2.8+2.6+2.9+2.6)/4 = 2.725 — in A, below pass_bot
        assert compute_bsk_map(a_bottom) < TIER_ZONES["A"]["pass_bot"]
        assert assign_bounty_type(a_bottom, "A")[0] != "SS"

        # Acc-dominant AND bsk >= pass_bot → SS.
        a_high = MapStub(
            beatmap_id=4,
            aim_stars=3.6, speed_stars=3.6, acc_stars=4.5, cons_stars=3.6,
        )
        # bsk = (3.6+3.6+4.5+3.6)/4 = 3.825 — above pass_bot AND acc-max
        assert compute_bsk_map(a_high) >= TIER_ZONES["A"]["pass_bot"]
        bt, cond = assign_bounty_type(a_high, "A")
        assert bt == "SS"
        assert cond == {"min_accuracy": 100.0}

    def test_accuracy_when_acc_max_but_below_ss_threshold(self):
        # B-tier pass_bot=2.45. Acc-axis-max but bsk<2.45 → Accuracy, not SS.
        m = MapStub(aim_stars=1.7, speed_stars=1.7, acc_stars=2.9, cons_stars=1.7)
        # bsk = (1.7+1.7+2.9+1.7)/4 = 2.0
        assert compute_bsk_map(m) == pytest.approx(2.0)
        assert compute_bsk_map(m) < TIER_ZONES["B"]["pass_bot"]
        bt, cond = assign_bounty_type(m, "B")
        assert bt == "Accuracy"
        assert cond == {"min_accuracy": 98.5}

    def test_metronome_uses_tier_met_mid(self):
        # B's met_mid = 2.18, window ±0.25. Map at exactly met_mid with all
        # axes equal (no axis-max, so it skips Acc/SS).
        m = _flat(1, TIER_ZONES["B"]["met_mid"])
        bt, cond = assign_bounty_type(m, "B")
        assert bt == "Metronome"
        assert cond == {"max_ur": 75}

    def test_metronome_open_uses_pool_p50(self):
        # Open's met_mid is anchored to BSK_POOL_MEDIAN ≈ 2.10 (NOT the math
        # midpoint 5.0). A bsk=2.10 map must classify as Metronome.
        assert TIER_ZONES["Open"]["met_mid"] == pytest.approx(BSK_POOL_MEDIAN, abs=0.01)
        m = _flat(1, BSK_POOL_MEDIAN)
        bt, _ = assign_bounty_type(m, "Open")
        assert bt == "Metronome"

    def test_metronome_open_does_not_match_math_midpoint(self):
        # Sanity: bsk=5.0 (old math midpoint) sits far above Open's met_mid →
        # must NOT be Metronome.
        m = _flat(1, 5.0)
        bt, _ = assign_bounty_type(m, "Open")
        assert bt != "Metronome"

    def test_mod_below_tier_mod_top(self):
        # B's mod_top = 1.95. bsk=1.80 < mod_top → Mod (all axes equal so no
        # axis dominance routes us to Accuracy).
        m = _flat(2, 1.80)
        assert compute_bsk_map(m) < TIER_ZONES["B"]["mod_top"]
        bt, cond = assign_bounty_type(m, "B")
        assert bt == "Mod"
        assert "required_mods" in cond
        assert cond["required_mods"][0] in ("HR", "HD", "DT")

    def test_pass_above_tier_pass_bot(self):
        # B's pass_bot = 2.45. bsk=2.55 >= pass_bot, all axes equal → Pass.
        m = _flat(3, 2.55)
        assert compute_bsk_map(m) >= TIER_ZONES["B"]["pass_bot"]
        bt, cond = assign_bounty_type(m, "B")
        assert bt == "Pass"
        assert cond == {}

    def test_fallback_first_fc(self):
        # Fallback fires when NO rule matches:
        #   length < 600
        #   not acc-axis-max
        #   not Metronome (|bsk - met_mid| > 0.25)
        #   not Mod (bsk >= mod_top)
        #   not Pass (bsk < pass_bot)
        # A zones: mod_top=2.95, met_mid=3.30, pass_bot=3.70.
        #   Mod:        bsk < 2.95
        #   Metronome:  [3.05, 3.55]
        #   Pass:       bsk >= 3.70
        # Gaps: [2.95, 3.05) and (3.55, 3.70). Pick bsk=3.62 with aim-axis-max.
        m = MapStub(
            beatmap_id=42,
            aim_stars=4.0, speed_stars=3.5, acc_stars=3.5, cons_stars=3.5,
        )
        # bsk = (4.0+3.5+3.5+3.5)/4 = 3.625 — A-range, in gap, aim-max
        assert compute_bsk_map(m) == pytest.approx(3.625)
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


class TestTierZones:
    def test_every_tier_has_all_zone_keys(self):
        for tier, zones in TIER_ZONES.items():
            for key in ("mod_top", "met_mid", "pass_bot"):
                assert key in zones, f"{tier} missing {key}"

    def test_zones_ordered(self):
        # mod_top < met_mid < pass_bot for every tier.
        for tier, z in TIER_ZONES.items():
            assert z["mod_top"] < z["met_mid"] < z["pass_bot"], (
                f"{tier} zones not monotonically ordered: {z}"
            )

    def test_c_b_a_zones_increase_with_tier(self):
        # Harder tiers should classify Mod/Pass at higher BSK than easier ones.
        for key in ("mod_top", "met_mid", "pass_bot"):
            c = TIER_ZONES["C"][key]
            b = TIER_ZONES["B"][key]
            a = TIER_ZONES["A"][key]
            assert c < b < a, f"{key}: C={c} B={b} A={a} — not strictly increasing"
