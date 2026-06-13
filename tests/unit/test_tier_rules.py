"""Unit tests for services.bounty.tier_rules.

Plan: unified-giggling-tiger.

Covers:
  * get_tier_for_hp thresholds via existing RANK_THRESHOLDS.
  * pick_for_tier filtering by star_rating range + bounty-type stratification.
  * assign_bounty_type rule order (Marathon → SS → Accuracy → Metronome → Mod → Pass → fallback).
  * conditions JSON round-trip.

Tier ranges (star_rating scale, June 2026):
  C    = [2.0, 4.5)    beginner / intermediate
  B    = [4.5, 7.0)    intermediate / advanced
  A    = [7.0, 10.0)   advanced / top
  Open = [0.0, 10.0)

All tests use a minimal MapStub instead of DuelMapPool so they have zero DB
deps. Tier/zone filtering uses star_rating.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass

import pytest

from services.bounty.tier_rules import (
    BOUNTY_TYPE_RULES,
    DUEL_POOL_MEDIAN,
    MAX_PER_TYPE,
    TIER_DUEL_RANGES,
    TIER_ZONES,
    assign_bounty_type,
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


def _flat(beatmap_id: int, sr: float, length: int = 180) -> MapStub:
    """All four axes = sr, star_rating = sr — consistent single-scale map stub.

    Most tests care only about star_rating (tier/zone filter). axis_stars are
    set equal so _axis_max returns "aim" (first max), avoiding accidental
    SS/Accuracy classification. Pass star_rating explicitly for zone-sensitive
    tests instead of using this helper.
    """
    return MapStub(
        beatmap_id=beatmap_id,
        aim_stars=sr, speed_stars=sr, acc_stars=sr, cons_stars=sr,
        star_rating=sr,
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


# ── pick_for_tier ───────────────────────────────────────────────────────────

class TestPickForTier:
    def test_returns_at_most_n_maps(self):
        # 12 maps all in C-range (sr=3.0) — picker must cap at 9.
        maps = [_flat(i, 3.0) for i in range(12)]
        picks = pick_for_tier(maps, "C", n=9)
        assert len(picks) == 9

    def test_filters_to_tier_range(self):
        # C=[2.0,4.5), B=[4.5,7.0), A=[7.0,10.0)
        srs = [2.5, 3.0, 4.0, 4.5, 5.5, 6.5, 7.0, 8.0, 9.5]
        maps = [_flat(i, sr) for i, sr in enumerate(srs)]
        c = pick_for_tier(maps, "C", n=99)
        b = pick_for_tier(maps, "B", n=99)
        a = pick_for_tier(maps, "A", n=99)
        assert {m.star_rating for m in c} == {2.5, 3.0, 4.0}
        assert {m.star_rating for m in b} == {4.5, 5.5, 6.5}
        assert {m.star_rating for m in a} == {7.0, 8.0, 9.5}

    def test_open_includes_everything(self):
        # Includes maps below C, in B, and in A — Open spans all SR.
        maps = [_flat(i, sr) for i, sr in enumerate([1.0, 5.0, 9.5])]
        assert len(pick_for_tier(maps, "Open", n=99)) == 3

    def test_unknown_tier_raises(self):
        with pytest.raises(ValueError):
            pick_for_tier([], "Z")

    def test_empty_pool_returns_empty(self):
        assert pick_for_tier([], "B") == []

    def test_small_pool_sorted_by_midpoint(self):
        # B midpoint = (4.5+7.0)/2 = 5.75. With ≤n maps, picker returns
        # everyone sorted by distance from midpoint — the closest first.
        maps = [
            _flat(1, 4.8),  # |4.8 - 5.75| = 0.95
            _flat(2, 5.7),  # |5.7 - 5.75| = 0.05 — closest
            _flat(3, 6.7),  # |6.7 - 5.75| = 0.95
        ]
        picks = pick_for_tier(maps, "B", n=9)
        assert picks[0].beatmap_id == 2

    def test_stratifies_rare_type(self):
        # 1 marathon + 30 Mod-zone filler in B-range. Phase 1 priority MUST
        # include the marathon even though it also falls in the Metronome SR
        # window (Marathon rule fires before Metronome).
        random.seed(0)
        marathon = _flat(999, 5.5, length=700)  # SR=5.5 in B, Marathon
        mods = [_flat(i, 4.8) for i in range(30)]  # SR=4.8 < mod_top=5.2
        picks = pick_for_tier([marathon, *mods], "B", n=9)
        assert marathon in picks

    def test_stratifies_each_type_when_all_present(self):
        # One map of each bounty type in B-range [4.5, 7.0). Picker must
        # include at least one of every type in its 9 picks.
        random.seed(1)
        zones = TIER_ZONES["B"]  # mod_top=5.2, met_mid=5.8, pass_bot=6.4
        # Marathon: in B, length ≥ 600 — fires before all other rules.
        marathon = _flat(1, 5.5, length=700)
        # SS: acc-axis-max AND sr >= pass_bot=6.4.
        ss = MapStub(beatmap_id=2,
                     aim_stars=4.0, speed_stars=4.0, acc_stars=5.0, cons_stars=4.0,
                     star_rating=6.5, length=180)
        # Accuracy: acc-axis-max but sr < pass_bot.
        accuracy = MapStub(beatmap_id=3,
                           aim_stars=3.0, speed_stars=3.0, acc_stars=4.0, cons_stars=3.0,
                           star_rating=5.5, length=180)
        # Metronome: |sr - met_mid| ≤ MET_WINDOW=0.5. All axes equal → aim-max.
        metronome = _flat(4, 5.8)
        # Mod: sr < mod_top=5.2. Also not in Metronome zone (|4.8-5.8|=1.0>0.5).
        mod = _flat(5, 4.8)
        # Pass: sr >= pass_bot=6.4, aim-max (not acc).
        pass_map = _flat(6, 6.5)
        all_maps = [marathon, ss, accuracy, metronome, mod, pass_map]
        # Filler: 20 Mod-zone maps so eligible pool > n.
        all_maps.extend(_flat(100 + j, 4.8) for j in range(20))

        picks = pick_for_tier(all_maps, "B", n=9)
        types_present = {assign_bounty_type(m, "B")[0] for m in picks}
        for expected in ("Marathon", "SS", "Accuracy", "Metronome", "Mod", "Pass"):
            assert expected in types_present, f"missing {expected} in {types_present}"

    def test_caps_dominant_type(self):
        # 50 Mod-zone maps in A-tier, nothing else. Phase 3 must top up to n=9.
        random.seed(2)
        # A mod_top=7.5 → sr=7.3 is Mod.
        mods = [_flat(i, 7.3) for i in range(50)]
        picks = pick_for_tier(mods, "A", n=9)
        assert len(picks) == 9
        types = {assign_bounty_type(m, "A")[0] for m in picks}
        assert types == {"Mod"}

    def test_cap_softens_mod_dominance_when_alternatives_exist(self):
        # 50 Mod-zone + 20 Pass maps in A-tier. Variety-aware Phase 3 must
        # keep Mod and Pass counts within ±1 of each other.
        random.seed(3)
        mods   = [_flat(i, 7.3)       for i in range(50)]  # sr < 7.5 → Mod
        passes = [_flat(100 + i, 9.0) for i in range(20)]  # sr=9.0 >= pass_bot=8.8 → Pass
        picks = pick_for_tier([*mods, *passes], "A", n=9)
        counts: dict[str, int] = {}
        for m in picks:
            bt, _ = assign_bounty_type(m, "A")
            counts[bt] = counts.get(bt, 0) + 1
        assert counts.get("Pass", 0) >= 1
        assert counts.get("Mod", 0) >= 1
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
        # SS fires when acc-axis-max AND sr >= TIER_ZONES[tier]["pass_bot"].
        # A-tier pass_bot = 8.8.
        a_bottom = MapStub(
            beatmap_id=2,
            aim_stars=3.0, speed_stars=2.8, acc_stars=3.5, cons_stars=2.8,
            star_rating=7.8,  # in A tier, but below pass_bot=8.8
        )
        assert a_bottom.star_rating < TIER_ZONES["A"]["pass_bot"]
        assert assign_bounty_type(a_bottom, "A")[0] != "SS"

        a_high = MapStub(
            beatmap_id=4,
            aim_stars=3.0, speed_stars=2.8, acc_stars=3.5, cons_stars=2.8,
            star_rating=9.0,  # in A tier, above pass_bot=8.8, acc-max
        )
        assert a_high.star_rating >= TIER_ZONES["A"]["pass_bot"]
        bt, cond = assign_bounty_type(a_high, "A")
        assert bt == "SS"
        assert cond == {"min_accuracy": 100.0}

    def test_accuracy_when_acc_max_but_below_ss_threshold(self):
        # B-tier pass_bot=6.4. Acc-axis-max but sr < 6.4 → Accuracy, not SS.
        m = MapStub(
            aim_stars=2.0, speed_stars=2.0, acc_stars=3.0, cons_stars=2.0,
            star_rating=5.5,
        )
        assert m.star_rating < TIER_ZONES["B"]["pass_bot"]
        bt, cond = assign_bounty_type(m, "B")
        assert bt == "Accuracy"
        assert cond == {"min_accuracy": 98.5}

    def test_metronome_uses_tier_met_mid(self):
        # B's met_mid = 5.8, window ±0.5. Map at exactly met_mid with all
        # axes equal (aim-max, so it skips Accuracy/SS).
        m = _flat(1, TIER_ZONES["B"]["met_mid"])
        bt, cond = assign_bounty_type(m, "B")
        assert bt == "Metronome"
        assert cond == {"max_ur": 75}

    def test_metronome_open_uses_pool_p50(self):
        # Open's met_mid equals DUEL_POOL_MEDIAN (pool SR median ≈ 4.0).
        assert TIER_ZONES["Open"]["met_mid"] == pytest.approx(DUEL_POOL_MEDIAN, abs=0.01)
        m = _flat(1, DUEL_POOL_MEDIAN)
        bt, _ = assign_bounty_type(m, "Open")
        assert bt == "Metronome"

    def test_metronome_open_does_not_match_math_midpoint(self):
        # sr=5.0 is 1.0 away from Open met_mid=4.0 (> MET_WINDOW=0.5) → not Metronome.
        m = _flat(1, 5.0)
        bt, _ = assign_bounty_type(m, "Open")
        assert bt != "Metronome"

    def test_mod_below_tier_mod_top(self):
        # B's mod_top = 5.2. sr=4.8 < 5.2 → Mod (aim-max, so not Accuracy/SS).
        m = _flat(2, 4.8)
        assert m.star_rating < TIER_ZONES["B"]["mod_top"]
        bt, cond = assign_bounty_type(m, "B")
        assert bt == "Mod"
        assert "required_mods" in cond
        assert cond["required_mods"][0] in ("HR", "HD", "DT")

    def test_pass_above_tier_pass_bot(self):
        # B's pass_bot = 6.4. sr=6.5 >= pass_bot, aim-max → Pass.
        m = _flat(3, 6.5)
        assert m.star_rating >= TIER_ZONES["B"]["pass_bot"]
        bt, cond = assign_bounty_type(m, "B")
        assert bt == "Pass"
        assert cond == {}

    def test_fallback_first_fc(self):
        # A zones: mod_top=7.5, met_mid=8.2, MET_WINDOW=0.5.
        #   Mod:        sr < 7.5
        #   Metronome:  sr ∈ [7.7, 8.7]
        #   Pass:       sr >= 8.8
        # Gap for First FC: sr ∈ [7.5, 7.7) — between Mod and Metronome.
        m = _flat(42, 7.6)  # SR=7.6, aim-max, length=180
        # Not Mod (7.6 >= 7.5), not Metronome (|7.6-8.2|=0.6>0.5), not Pass (<8.8)
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


# ── TIER_DUEL_RANGES invariants ──────────────────────────────────────────────

class TestTierDuelRanges:
    def test_open_spans_full_range(self):
        lo, hi = TIER_DUEL_RANGES["Open"]
        assert lo == 0.0
        assert hi >= 10.0

    def test_c_b_a_partition_is_contiguous(self):
        c_lo, c_hi = TIER_DUEL_RANGES["C"]
        b_lo, b_hi = TIER_DUEL_RANGES["B"]
        a_lo, _   = TIER_DUEL_RANGES["A"]
        assert c_hi == b_lo  # no gap between C and B
        assert b_hi == a_lo  # no gap between B and A

    def test_duel_pool_median_constant_in_open_range(self):
        lo, hi = TIER_DUEL_RANGES["Open"]
        assert lo <= DUEL_POOL_MEDIAN < hi


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
        # Harder tiers should classify Mod/Pass at higher DUEL than easier ones.
        for key in ("mod_top", "met_mid", "pass_bot"):
            c = TIER_ZONES["C"][key]
            b = TIER_ZONES["B"][key]
            a = TIER_ZONES["A"][key]
            assert c < b < a, f"{key}: C={c} B={b} A={a} — not strictly increasing"
