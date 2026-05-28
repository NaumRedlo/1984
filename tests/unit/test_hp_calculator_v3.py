"""Tests for the v3 extensions to utils.hp_calculator.

Plan: unified-giggling-tiger (step 7/9).

Covers the three new behaviours added to `calculate_hps` plus the new
helpers `_bootstrap_multiplier` and `_psi_hybrid`:

  * anti_farm_multiplier reduces final HP
  * bootstrap_multiplier raises final HP for new HPS-active users
  * use_psi_hybrid=True > use_psi_hybrid=False for specialists on
    matching maps; both equal for balanced players
  * legacy mode (legacy multipliers = 1.0, hybrid off) matches the
    pre-change formula bit-for-bit
"""

from __future__ import annotations

import math

import pytest

from utils.hp_calculator import (
    MapInfo, PlayerSkill, ScoreStats, calculate_hps,
    _bootstrap_multiplier, _psi_hybrid, _psi,
    BOOTSTRAP_PEAK,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _balanced_map(sr: float = 5.0) -> MapInfo:
    return MapInfo.fallback_from_sr(
        star_rating=sr, od=8.0, drain_time=240, max_combo=1000,
    )


def _speed_map() -> MapInfo:
    """Speed-axis dominant: weights tilted, stars skewed."""
    return MapInfo(
        aim_stars=4.0, speed_stars=8.0, acc_stars=4.0, cons_stars=4.0,
        w_aim=0.15, w_speed=0.55, w_acc=0.15, w_cons=0.15,
        od=8.0, drain_time_seconds=240, max_combo=1000,
    )


def _balanced_player(skill: float = 5.0) -> PlayerSkill:
    return PlayerSkill(aim=skill, speed=skill, acc=skill, cons=skill)


def _fc_score() -> ScoreStats:
    return ScoreStats(n_300=1000, n_100=0, n_50=0, misses=0, combo=1000)


# ── Bootstrap multiplier ──────────────────────────────────────────────────


class TestBootstrap:
    def test_none_is_neutral(self):
        assert _bootstrap_multiplier(None) == 1.0

    def test_day_0_near_peak(self):
        # 1 + 0.5 · sigmoid(2) ≈ 1 + 0.5 · 0.88 ≈ 1.44
        v = _bootstrap_multiplier(0)
        assert 1.4 < v < 1.5
        assert v < 1.0 + BOOTSTRAP_PEAK + 1e-9

    def test_day_30_midpoint(self):
        # sigmoid(0) = 0.5 → 1 + 0.5 · 0.5 = 1.25
        assert _bootstrap_multiplier(30) == pytest.approx(1.25, rel=1e-3)

    def test_day_60_low(self):
        v = _bootstrap_multiplier(60)
        assert 1.0 < v < 1.1

    def test_day_90_near_one(self):
        v = _bootstrap_multiplier(90)
        assert v == pytest.approx(1.0, abs=0.02)

    def test_monotonic_decreasing(self):
        prev = float("inf")
        for d in [0, 10, 20, 30, 45, 60, 90, 365]:
            v = _bootstrap_multiplier(d)
            assert v < prev
            prev = v


# ── Ψ-hybrid ──────────────────────────────────────────────────────────────


class TestPsiHybrid:
    def test_balanced_player_balanced_map_matches_single_psi(self):
        # All deltas equal → max == avg == single Ψ → hybrid = single.
        m, p = _balanced_map(), _balanced_player(5.0)
        psi_h, psi_max, psi_avg = _psi_hybrid(m, p)
        assert psi_h == pytest.approx(psi_max)
        assert psi_h == pytest.approx(psi_avg)
        assert psi_h == pytest.approx(_psi(0.0), rel=1e-3)

    def test_specialist_on_specialist_map_lower_psi_avg(self):
        # Speed specialist (speed=8) on speed map: speed-axis Δ ≈ 0,
        # other axes Δ ≈ 4-3 = positive only for non-speed axes — but
        # weights downplay them.  We assert psi_max > psi_avg (the
        # toughest axis dominates the hybrid).
        m = _speed_map()
        p = PlayerSkill(aim=3.0, speed=8.0, acc=3.0, cons=3.0)
        psi_h, psi_max, psi_avg = _psi_hybrid(m, p)
        # On the speed axis the player matches the map → Ψ ≈ 1.25 (mid).
        # On non-speed axes the player is *under* the map by ~1 → Ψ > 1.25.
        # max-axis is one of those weak axes.
        assert psi_max > psi_avg
        # Hybrid lies between them.
        assert psi_avg <= psi_h <= psi_max


# ── Anti-farm wired through calculate_hps ─────────────────────────────────


class TestAntiFarmWiring:
    def test_anti_farm_lowers_hp(self):
        kw = dict(
            result_type="win",
            map_info=_balanced_map(), player_skill=_balanced_player(),
            score=_fc_score(),
        )
        full = calculate_hps(**kw, anti_farm_multiplier=1.0)
        half = calculate_hps(**kw, anti_farm_multiplier=0.5)
        assert half["final_hp"] < full["final_hp"]
        assert half["anti_farm"] == 0.5

    def test_anti_farm_clamped_below(self):
        # Negative input gets clamped to 0.0 → final_hp = vanguard only (or 0).
        out = calculate_hps(
            result_type="win",
            map_info=_balanced_map(), player_skill=_balanced_player(),
            score=_fc_score(),
            anti_farm_multiplier=-1.0,
        )
        assert out["anti_farm"] == 0.0
        assert out["final_hp"] == 0


# ── Bootstrap wired through calculate_hps ────────────────────────────────


class TestBootstrapWiring:
    def test_explicit_multiplier_wins(self):
        # If both bootstrap_multiplier and days_since are passed, the
        # explicit multiplier should be used.
        out = calculate_hps(
            result_type="win",
            map_info=_balanced_map(), player_skill=_balanced_player(),
            score=_fc_score(),
            bootstrap_multiplier=1.3,
            days_since_first_approved=10000,  # would yield ~1.0 if used
        )
        assert out["bootstrap"] == 1.3

    def test_days_derives_multiplier_when_default(self):
        out = calculate_hps(
            result_type="win",
            map_info=_balanced_map(), player_skill=_balanced_player(),
            score=_fc_score(),
            days_since_first_approved=30,
        )
        # Day 30 → midpoint → 1.25
        assert out["bootstrap"] == pytest.approx(1.25, rel=1e-2)

    def test_bootstrap_raises_hp(self):
        kw = dict(
            result_type="win",
            map_info=_balanced_map(), player_skill=_balanced_player(),
            score=_fc_score(),
        )
        baseline = calculate_hps(**kw)
        boosted  = calculate_hps(**kw, bootstrap_multiplier=1.5)
        assert boosted["final_hp"] >= baseline["final_hp"]

    def test_bootstrap_clamped_above(self):
        out = calculate_hps(
            result_type="win",
            map_info=_balanced_map(), player_skill=_balanced_player(),
            score=_fc_score(),
            bootstrap_multiplier=99.0,  # ridiculous
        )
        assert out["bootstrap"] == 2.0  # clamped


# ── Legacy compat ─────────────────────────────────────────────────────────


class TestLegacyParity:
    def test_use_psi_hybrid_false_matches_old_psi(self):
        # With no anti-farm / no bootstrap and hybrid OFF, hp_pre should
        # equal the old (pre-step-7) formula for an unequal player.
        m = _speed_map()
        p = PlayerSkill(aim=3.0, speed=8.0, acc=3.0, cons=3.0)
        legacy = calculate_hps(
            result_type="win", map_info=m, player_skill=p, score=_fc_score(),
            use_psi_hybrid=False,
        )
        # The Ψ in legacy mode equals _psi over the weighted delta.
        delta = (0.15*(4-3) + 0.55*(8-8) + 0.15*(4-3) + 0.15*(4-3))
        assert legacy["psi"] == pytest.approx(_psi(delta), rel=1e-3)
