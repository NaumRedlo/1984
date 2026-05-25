"""Tests for HPS v2 (Math Manifest) — calculate_hps and its parts."""

import math

import pytest

from utils.hp_calculator import (
    HPS_BASE,
    HPS_VANGUARD,
    MapInfo,
    PlayerSkill,
    RANK_THRESHOLDS,
    RESULT_TYPE_MULTIPLIER,
    ScoreStats,
    _c_pen,
    _lambda,
    _omega,
    _phi,
    _psi,
    calculate_hps,
    get_next_rank_info,
    get_rank_for_hp,
)


# ── Module helpers ───────────────────────────────────────────────────────────

class TestPhi:
    def test_zero_bsk_returns_floor(self):
        assert _phi(0.0) == 0.5

    def test_negative_clamped(self):
        # If a degenerate map sneaks in with BSK<0 we don't want a complex number.
        assert _phi(-1.0) == 0.5

    def test_reference_values_match_manifest(self):
        # Φ implementation matches the literal formula, so this guards against
        # accidental algebra mistakes during refactors.
        assert _phi(4.0) == pytest.approx(0.5 + 0.05 * 4 ** 1.8, abs=1e-6)
        assert _phi(8.0) == pytest.approx(0.5 + 0.05 * 8 ** 1.8, abs=1e-6)
        # Concrete numbers — the Manifest mentions ~1.1× at 4★ and ~2.3× at 8★.
        # Actual 8★ value is closer to 2.6 (Manifest examples are rounded);
        # we assert the formula-true range, not the documentation's quote.
        assert 1.05 < _phi(4.0) < 1.15
        assert 2.50 < _phi(8.0) < 2.70


class TestPsi:
    def test_zero_delta_is_one_and_a_quarter(self):
        # Ψ(0) = 0.5 + 1.5/2 = 1.25
        assert _psi(0.0) == pytest.approx(1.25)

    def test_floor_at_extreme_negative(self):
        # Big negative Δ ⇒ Ψ → 0.5 (the deep-farm punishment floor).
        assert _psi(-10.0) == pytest.approx(0.5, abs=0.01)

    def test_ceiling_at_extreme_positive(self):
        # Big positive Δ ⇒ Ψ → 2.0.
        assert _psi(10.0) == pytest.approx(2.0, abs=0.01)

    def test_reference_minus_two_around_057(self):
        # Manifest: Δ=-2 → 0.57.
        assert _psi(-2.0) == pytest.approx(0.57, abs=0.01)

    def test_reference_plus_two_around_193(self):
        # Manifest: Δ=+2 → 1.93.
        assert _psi(2.0) == pytest.approx(1.93, abs=0.01)


class TestOmega:
    def test_none_is_neutral(self):
        # No UR data → don't reward and don't punish.
        assert _omega(None) == 1.0

    def test_ur100_is_one(self):
        assert _omega(100.0) == pytest.approx(1.0)

    def test_ur150_punishes(self):
        # Manifest: UR=150 → 0.51
        assert _omega(150.0) == pytest.approx(math.exp(-50 / 75))
        assert _omega(150.0) < 0.55

    def test_ur65_rewards(self):
        # Manifest: UR=65 → 1.59
        assert _omega(65.0) == pytest.approx(math.exp(35 / 75))
        assert _omega(65.0) > 1.55


class TestLambda:
    def test_minimum_floor(self):
        # Λ never goes below 0.4 even for nonsensically short maps.
        assert _lambda(0) == pytest.approx(0.6)  # ln(1)+0.6 = 0.6
        assert _lambda(0) >= 0.4

    def test_one_minute_around_one(self):
        # t=60s → Λ ≈ ln(1.4) + 0.6 ≈ 0.94
        assert _lambda(60) == pytest.approx(math.log(1.4) + 0.6)

    def test_marathon_grows_unboundedly(self):
        # Unlike the legacy LSS, v2 Λ has no cap.
        assert _lambda(1200) > _lambda(600)
        assert _lambda(600) > _lambda(180)


class TestCpenV2:
    def test_fc_no_miss(self):
        assert _c_pen(1000, 1000, 0) == pytest.approx(1.0)

    def test_misses_decay_geometrically(self):
        assert _c_pen(1000, 1000, 3) == pytest.approx(0.92 ** 3)

    def test_partial_combo_uses_sqrt(self):
        # 25% combo retention → sqrt(0.25) = 0.5
        assert _c_pen(250, 1000, 0) == pytest.approx(0.5)

    def test_zero_max_combo_safe(self):
        # Defensive against bad map data.
        assert _c_pen(500, 0, 0) == 1.0
        assert _c_pen(500, 0, 4) == pytest.approx(0.92 ** 4)


class TestResultTypeMultiplier:
    def test_known_values(self):
        assert RESULT_TYPE_MULTIPLIER["win"] == 1.5
        assert RESULT_TYPE_MULTIPLIER["condition"] == 1.0
        assert RESULT_TYPE_MULTIPLIER["partial"] == 0.5
        assert RESULT_TYPE_MULTIPLIER["participation"] == 0.2


# ── Ranks v2 ─────────────────────────────────────────────────────────────────

class TestRanksV2:
    def test_candidate_floor(self):
        assert get_rank_for_hp(0) == "Candidate"
        assert get_rank_for_hp(249) == "Candidate"

    def test_party_member(self):
        assert get_rank_for_hp(250) == "Member"
        assert get_rank_for_hp(749) == "Member"

    def test_inspector(self):
        assert get_rank_for_hp(750) == "Inspector"
        assert get_rank_for_hp(1499) == "Inspector"

    def test_high_commissioner(self):
        assert get_rank_for_hp(1500) == "Commissioner"
        assert get_rank_for_hp(2999) == "Commissioner"

    def test_big_brother(self):
        assert get_rank_for_hp(3000) == "Big Brother"
        assert get_rank_for_hp(999_999) == "Big Brother"

    def test_thresholds_table_sorted_descending(self):
        # Failsafe — get_rank_for_hp relies on the list being sorted high→low.
        thresholds = [t for t, _ in RANK_THRESHOLDS]
        assert thresholds == sorted(thresholds, reverse=True)

    def test_next_rank_info_at_candidate(self):
        info = get_next_rank_info(0)
        assert info["current"] == "Candidate"
        assert info["next"] == "Member"
        assert info["hp_needed"] == 250

    def test_next_rank_info_at_top(self):
        info = get_next_rank_info(5000)
        assert info["current"] == "Big Brother"
        assert info["next"] is None


# ── Full formula ─────────────────────────────────────────────────────────────

def _balanced_map(*, sr=5.0, od=8.0, drain=180, max_combo=1000) -> MapInfo:
    """All four axes equal to sr, equal weights — useful as a starting point."""
    return MapInfo(
        aim_stars=sr, speed_stars=sr, acc_stars=sr, cons_stars=sr,
        w_aim=0.25, w_speed=0.25, w_acc=0.25, w_cons=0.25,
        od=od, drain_time_seconds=drain, max_combo=max_combo,
    )


def _flat_player(skill=5.0) -> PlayerSkill:
    return PlayerSkill(aim=skill, speed=skill, acc=skill, cons=skill)


class TestCalculateHpsV2:
    def test_returns_full_breakdown(self):
        result = calculate_hps(
            result_type="condition",
            map_info=_balanced_map(),
            player_skill=_flat_player(),
            score=ScoreStats(n_300=500, n_100=10, n_50=0, misses=0, combo=1000),
        )
        # Sanity: all expected keys are present.
        for key in (
            "base", "phi", "psi", "omega", "lambda", "c_pen", "r",
            "vanguard", "ur_est", "bsk_map", "delta", "hp_pre", "final_hp",
            "calculated_at",
        ):
            assert key in result

    def test_balanced_player_balanced_map_delta_zero(self):
        # When BSK_user matches BSK_map exactly, Ψ should be 1.25.
        result = calculate_hps(
            result_type="condition",
            map_info=_balanced_map(sr=5.0),
            player_skill=_flat_player(5.0),
            score=ScoreStats(n_300=500, n_100=10, n_50=0, misses=0, combo=1000),
        )
        assert result["delta"] == pytest.approx(0.0)
        assert result["psi"] == pytest.approx(1.25)

    def test_unknown_result_type_yields_zero_hp(self):
        # R = 0 ⇒ HP_pre = 0 ⇒ final 0 + Vanguard if first.
        result = calculate_hps(
            result_type="rejected",
            map_info=_balanced_map(),
            player_skill=_flat_player(),
            score=ScoreStats(n_300=500, n_100=10, n_50=0, misses=0, combo=1000),
        )
        assert result["r"] == 0.0
        assert result["final_hp"] == 0

    def test_vanguard_added_only_when_first(self):
        common = dict(
            result_type="condition",
            map_info=_balanced_map(),
            player_skill=_flat_player(),
            score=ScoreStats(n_300=500, n_100=10, n_50=0, misses=0, combo=1000),
        )
        no_vg = calculate_hps(**common, is_first_submission=False)
        vg    = calculate_hps(**common, is_first_submission=True)
        assert vg["final_hp"] - no_vg["final_hp"] == HPS_VANGUARD
        assert no_vg["vanguard"] == 0
        assert vg["vanguard"] == HPS_VANGUARD

    def test_win_pays_more_than_condition(self):
        common = dict(
            map_info=_balanced_map(),
            player_skill=_flat_player(),
            score=ScoreStats(n_300=500, n_100=10, n_50=0, misses=0, combo=1000),
        )
        win  = calculate_hps(result_type="win", **common)
        cond = calculate_hps(result_type="condition", **common)
        # win = 1.5×, condition = 1.0× → ratio 1.5.
        assert win["final_hp"] > cond["final_hp"]

    def test_overskilled_player_gets_punished(self):
        # SR=4 map vs a BSK 7 player — Δ = -3 → Ψ ≈ 0.51.  Compare against
        # the same player on a matched-skill map.
        easy = calculate_hps(
            result_type="condition",
            map_info=_balanced_map(sr=4.0),
            player_skill=_flat_player(7.0),
            score=ScoreStats(n_300=500, n_100=0, n_50=0, misses=0, combo=1000),
        )
        matched = calculate_hps(
            result_type="condition",
            map_info=_balanced_map(sr=7.0),
            player_skill=_flat_player(7.0),
            score=ScoreStats(n_300=500, n_100=0, n_50=0, misses=0, combo=1000),
        )
        assert easy["final_hp"] < matched["final_hp"]

    def test_skewed_map_skewed_player(self):
        # Speed-heavy map (speed=8, aim=2, w_speed dominant) — only the
        # speed axis of the player should matter for Δ.
        map_info = MapInfo(
            aim_stars=2.0, speed_stars=8.0, acc_stars=4.0, cons_stars=4.0,
            w_aim=0.05, w_speed=0.85, w_acc=0.05, w_cons=0.05,
            od=8.0, drain_time_seconds=180, max_combo=1000,
        )
        bad_speed = PlayerSkill(aim=9.0, speed=3.0, acc=9.0, cons=9.0)
        good_speed = PlayerSkill(aim=3.0, speed=9.0, acc=3.0, cons=3.0)
        score = ScoreStats(n_300=500, n_100=0, n_50=0, misses=0, combo=1000)

        result_bad  = calculate_hps(result_type="condition", map_info=map_info,
                                       player_skill=bad_speed,  score=score)
        result_good = calculate_hps(result_type="condition", map_info=map_info,
                                       player_skill=good_speed, score=score)
        # bad_speed: Δ ≈ +5 (map is way over their speed head) → big Ψ.
        # good_speed: Δ ≈ -1 → small Ψ.
        assert result_bad["psi"] > result_good["psi"]
        assert result_bad["final_hp"] > result_good["final_hp"]

    def test_ur_override_skips_recompute(self):
        # When ur_est_override is supplied, n_300/n_100/n_50 stop mattering
        # for Ω (they still feed nothing else, so the result should be stable).
        common = dict(
            result_type="condition",
            map_info=_balanced_map(),
            player_skill=_flat_player(),
            ur_est_override=80.0,
        )
        a = calculate_hps(score=ScoreStats(0, 0, 0, misses=0, combo=1000), **common)
        b = calculate_hps(score=ScoreStats(500, 0, 0, misses=0, combo=1000), **common)
        assert a["omega"] == b["omega"]
        # And Ω matches what _omega returns for UR=80.
        assert a["omega"] == pytest.approx(round(math.exp(20 / 75), 4))

    def test_misses_decay_final_hp(self):
        # Same scenario, different miss counts — final_hp should drop.
        common = dict(
            result_type="condition",
            map_info=_balanced_map(),
            player_skill=_flat_player(),
        )
        clean = calculate_hps(
            score=ScoreStats(500, 10, 0, misses=0, combo=1000), **common,
        )
        dirty = calculate_hps(
            score=ScoreStats(500, 10, 0, misses=5, combo=950), **common,
        )
        assert dirty["final_hp"] < clean["final_hp"]

    def test_no_hits_omega_neutral(self):
        # participation result with N_hits=0 → UR = None → Ω = 1.0.
        result = calculate_hps(
            result_type="participation",
            map_info=_balanced_map(),
            player_skill=_flat_player(),
            score=ScoreStats(0, 0, 0, misses=0, combo=0),
        )
        assert result["ur_est"] is None
        assert result["omega"] == 1.0

    def test_floor_non_negative(self):
        # Combinations that drive HP_pre to ~0 should still produce a clean 0.
        result = calculate_hps(
            result_type="participation",
            map_info=_balanced_map(sr=1.0),
            player_skill=_flat_player(10.0),  # Δ very negative
            score=ScoreStats(0, 0, 0, misses=20, combo=0),
        )
        assert result["final_hp"] >= 0

    def test_base_default_is_60(self):
        # Sanity: HPS_BASE matches the Manifest default and the formula
        # actually uses it.
        assert HPS_BASE == 60
        result = calculate_hps(
            result_type="condition",
            map_info=_balanced_map(),
            player_skill=_flat_player(),
            score=ScoreStats(500, 10, 0, misses=0, combo=1000),
        )
        assert result["base"] == 60


class TestMapInfoFallback:
    def test_fallback_uses_sr_uniformly(self):
        info = MapInfo.fallback_from_sr(star_rating=6.5, od=7.0, drain_time=200, max_combo=800)
        assert info.aim_stars == info.speed_stars == info.acc_stars == info.cons_stars == 6.5
        assert info.w_aim == info.w_speed == info.w_acc == info.w_cons == 0.25
        assert info.od == 7.0
        assert info.drain_time_seconds == 200
        assert info.max_combo == 800
