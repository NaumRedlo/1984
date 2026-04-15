import pytest
import math

from utils.hp_calculator import (
    get_rank_for_hp,
    get_next_rank_info,
    calculate_tsf,
    calculate_dynamic_dm,
    calculate_log_lss,
    calculate_relativity_factor,
    calculate_bonuses,
    calculate_hps,
    _clamp,
    RANK_THRESHOLDS,
    BASE_HP_TABLE,
)


class TestClamp:
    def test_within_range(self):
        assert _clamp(5.0, 0.0, 10.0) == 5.0

    def test_below_min(self):
        assert _clamp(-1.0, 0.0, 10.0) == 0.0

    def test_above_max(self):
        assert _clamp(15.0, 0.0, 10.0) == 10.0

    def test_at_boundary(self):
        assert _clamp(0.0, 0.0, 10.0) == 0.0
        assert _clamp(10.0, 0.0, 10.0) == 10.0


class TestGetRankForHp:
    def test_candidate(self):
        assert get_rank_for_hp(0) == "Candidate"
        assert get_rank_for_hp(250) == "Candidate"

    def test_party_member(self):
        assert get_rank_for_hp(251) == "Party Member"
        assert get_rank_for_hp(750) == "Party Member"

    def test_inspector(self):
        assert get_rank_for_hp(751) == "Inspector"
        assert get_rank_for_hp(1500) == "Inspector"

    def test_high_commissioner(self):
        assert get_rank_for_hp(1501) == "High Commissioner"
        assert get_rank_for_hp(3000) == "High Commissioner"

    def test_big_brother(self):
        assert get_rank_for_hp(3001) == "Big Brother"
        assert get_rank_for_hp(99999) == "Big Brother"

    def test_negative_hp(self):
        assert get_rank_for_hp(-10) == "Candidate"


class TestGetNextRankInfo:
    def test_candidate_progress(self):
        info = get_next_rank_info(100)
        assert info["current"] == "Candidate"
        assert info["next"] == "Party Member"
        assert info["hp_needed"] == 151

    def test_at_max_rank(self):
        info = get_next_rank_info(5000)
        assert info["current"] == "Big Brother"
        assert info["next"] is None
        assert info["hp_needed"] == 0

    def test_exact_threshold(self):
        info = get_next_rank_info(251)
        assert info["current"] == "Party Member"
        assert info["next"] == "Inspector"
        assert info["hp_needed"] == 500

    def test_zero_hp(self):
        info = get_next_rank_info(0)
        assert info["current"] == "Candidate"
        assert info["hp_needed"] == 251


class TestCalculateTsf:
    def test_zero_data_returns_ones(self):
        result = calculate_tsf(0, 0, 0, 0, 0, 0)
        assert result["value"] == 1.0
        for key in ("cs", "od", "ar", "hp", "bpm", "combo"):
            assert result[key] == 1.0

    def test_typical_map(self):
        result = calculate_tsf(cs=4.0, od=8.0, ar=9.0, hp=6.0, bpm=180, max_combo=800)
        assert 0.85 <= result["value"] <= 1.35
        assert result["ar"] == 1.0  # ar=9 дает минимальное отклонение

    def test_high_values_clamp(self):
        result = calculate_tsf(cs=10, od=15, ar=0, hp=15, bpm=500, max_combo=5000)
        assert result["cs"] <= 1.35
        assert result["od"] <= 1.35
        assert result["bpm"] <= 1.35
        assert result["combo"] <= 1.25

    def test_keys_present(self):
        result = calculate_tsf(4, 8, 9, 5, 180, 1000)
        expected_keys = {"value", "cs", "od", "ar", "hp", "bpm", "combo"}
        assert set(result.keys()) == expected_keys


class TestCalculateDynamicDm:
    def test_beginner(self):
        result = calculate_dynamic_dm(3.0)
        assert result["category"] == "Beginner"
        assert result["stars"] == 3.0

    def test_legendary(self):
        result = calculate_dynamic_dm(9.0)
        assert result["category"] == "Legendary"
        assert result["value"] <= 2.0

    def test_clamp_minimum(self):
        result = calculate_dynamic_dm(1.0)
        assert result["value"] >= 0.8

    def test_categories(self):
        assert calculate_dynamic_dm(4.9)["category"] == "Beginner"
        assert calculate_dynamic_dm(5.0)["category"] == "Basic"
        assert calculate_dynamic_dm(6.0)["category"] == "Advanced"
        assert calculate_dynamic_dm(7.0)["category"] == "Expert"
        assert calculate_dynamic_dm(8.0)["category"] == "Legendary"

    def test_formula(self):
        result = calculate_dynamic_dm(6.5)
        expected = round((6.5 * 0.24) - 0.16, 3)
        assert result["value"] == expected


class TestCalculateLogLss:
    def test_sprint(self):
        result = calculate_log_lss(90)
        assert result["category"] == "Sprint"
        assert result["duration"] == "1:30"
        assert result["seconds"] == 90

    def test_standard(self):
        result = calculate_log_lss(200)
        assert result["category"] == "Standard"

    def test_titan(self):
        result = calculate_log_lss(700)
        assert result["category"] == "Titan"

    def test_zero_defaults_to_30(self):
        result = calculate_log_lss(0)
        assert result["seconds"] == 30

    def test_negative_defaults_to_30(self):
        result = calculate_log_lss(-50)
        assert result["seconds"] == 30

    def test_clamp_range(self):
        assert calculate_log_lss(10)["value"] >= 0.7
        assert calculate_log_lss(100000)["value"] <= 2.0

    def test_time_format(self):
        result = calculate_log_lss(185)
        assert result["duration"] == "3:05"


class TestCalculateRelativityFactor:
    def test_no_stats(self):
        result = calculate_relativity_factor(5000, {"p25": 0, "p75": 0})
        assert result["value"] == 1.0
        assert result["category"] == "Average"

    def test_top_player(self):
        stats = {"p25": 1000, "p40": 3000, "p60": 5000, "p75": 8000}
        result = calculate_relativity_factor(9000, stats)
        assert result["value"] == 0.80
        assert result["category"] == "Top Player"

    def test_newcomer(self):
        stats = {"p25": 1000, "p40": 3000, "p60": 5000, "p75": 8000}
        result = calculate_relativity_factor(500, stats)
        assert result["value"] == 1.20
        assert result["category"] == "Newcomer"

    def test_exact_threshold(self):
        stats = {"p25": 1000, "p40": 3000, "p60": 5000, "p75": 8000}
        result = calculate_relativity_factor(8000, stats)
        assert result["category"] == "Top Player"

    def test_missing_keys_default_zero(self):
        result = calculate_relativity_factor(5000, {})
        assert result["value"] == 1.0


class TestCalculateBonuses:
    def test_no_bonuses(self):
        result = calculate_bonuses(95.0, False, False, False)
        assert result["total"] == 0
        assert result["list"] == []

    def test_flawless(self):
        result = calculate_bonuses(100.0, False, False, False)
        assert result["total"] == 25
        assert result["list"][0]["name"] == "Flawless Execution"

    def test_elite_precision(self):
        result = calculate_bonuses(99.5, False, False, False)
        assert result["total"] == 15
        assert result["list"][0]["name"] == "Elite Precision"

    def test_flawless_overrides_elite(self):
        # 100% дает Flawless, не Elite
        result = calculate_bonuses(100.0, False, False, False)
        names = [b["name"] for b in result["list"]]
        assert "Flawless Execution" in names
        assert "Elite Precision" not in names

    def test_all_bonuses_cap(self):
        result = calculate_bonuses(100.0, True, True, True)
        assert result["total"] == 50
        names = [b["name"] for b in result["list"]]
        assert "Cap Applied" in names

    def test_first_submission(self):
        result = calculate_bonuses(50.0, True, False, False)
        assert result["total"] == 15

    def test_combined_no_cap(self):
        result = calculate_bonuses(99.0, True, False, False)
        assert result["total"] == 30
        names = [b["name"] for b in result["list"]]
        assert "Cap Applied" not in names


class TestCalculateHps:
    def test_win_basic(self):
        stats = {"p25": 1000, "p40": 3000, "p60": 5000, "p75": 8000}
        result = calculate_hps("win", 6.0, 180, 4000, stats)
        assert result["base_hp"] == 100
        assert "final_hp" in result
        assert "calculated_at" in result
        assert result["final_hp"] > 0

    def test_unknown_type_defaults(self):
        result = calculate_hps("unknown", 5.0, 120, 3000, {})
        assert result["base_hp"] == 10

    def test_case_insensitive(self):
        result = calculate_hps("WIN", 5.0, 120, 3000, {})
        assert result["base_hp"] == 100

    def test_all_result_types(self):
        for rtype, expected_base in BASE_HP_TABLE.items():
            result = calculate_hps(rtype, 5.0, 120, 3000, {})
            assert result["base_hp"] == expected_base

    def test_multiplier_structure(self):
        stats = {"p25": 1000, "p40": 3000, "p60": 5000, "p75": 8000}
        result = calculate_hps(
            "win", 6.5, 200, 4000, stats,
            cs=4.0, od=8.0, ar=9.0, hp_drain=5.0, bpm=180, max_combo=1000,
        )
        dm = result["dynamic_dm"]["value"]
        lss = result["log_lss"]["value"]
        rf = result["relativity_factor"]["value"]
        tsf = result["tsf"]["value"]
        expected_mult = round(dm * lss * rf * tsf, 3)
        assert result["total_multiplier"] == expected_mult

    def test_bonuses_included(self):
        result = calculate_hps(
            "win", 6.0, 180, 4000, {},
            accuracy=100.0, is_first_submission=True,
        )
        assert result["bonuses"]["total"] > 0
