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
    MAX_HP_PER_SUBMISSION,
    get_division_for_hp,
    get_division_for_conservative,
    BSK_DIVISION_INDEX,
    SEASON_BONUS_HPS,
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
        assert get_rank_for_hp(299) == "Candidate"

    def test_member(self):
        assert get_rank_for_hp(300) == "Member"
        assert get_rank_for_hp(899) == "Member"

    def test_inspector(self):
        assert get_rank_for_hp(900) == "Inspector"
        assert get_rank_for_hp(1999) == "Inspector"

    def test_commissioner(self):
        assert get_rank_for_hp(2000) == "Commissioner"
        assert get_rank_for_hp(4499) == "Commissioner"

    def test_big_brother(self):
        assert get_rank_for_hp(4500) == "Big Brother"
        assert get_rank_for_hp(99999) == "Big Brother"

    def test_negative_hp(self):
        assert get_rank_for_hp(-10) == "Candidate"


class TestGetNextRankInfo:
    def test_candidate_progress(self):
        info = get_next_rank_info(100)
        assert info["current"] == "Candidate"
        assert info["next"] == "Member"
        assert info["hp_needed"] == 200

    def test_at_max_rank(self):
        info = get_next_rank_info(5000)
        assert info["current"] == "Big Brother"
        assert info["next"] is None
        assert info["hp_needed"] == 0

    def test_exact_threshold(self):
        info = get_next_rank_info(300)
        assert info["current"] == "Member"
        assert info["next"] == "Inspector"
        assert info["hp_needed"] == 600

    def test_zero_hp(self):
        info = get_next_rank_info(0)
        assert info["current"] == "Candidate"
        assert info["hp_needed"] == 300


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
        assert result["base_hp"] == 80
        assert "final_hp" in result
        assert "calculated_at" in result
        assert result["final_hp"] > 0

    def test_unknown_type_defaults(self):
        result = calculate_hps("unknown", 5.0, 120, 3000, {})
        assert result["base_hp"] == 10

    def test_case_insensitive(self):
        result = calculate_hps("WIN", 5.0, 120, 3000, {})
        assert result["base_hp"] == 80

    def test_all_result_types(self):
        for rtype, expected_base in BASE_HP_TABLE.items():
            result = calculate_hps(rtype, 5.0, 120, 3000, {})
            assert result["base_hp"] == expected_base

    def test_cap_applied(self):
        stats = {"p25": 0, "p40": 0, "p60": 0, "p75": 0}
        result = calculate_hps("win", 9.0, 600, 0, stats, accuracy=100.0, is_first_submission=True, extra_challenge=True)
        assert result["final_hp"] <= MAX_HP_PER_SUBMISSION

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


class TestGetDivisionForHp:
    def test_candidate_iii(self):
        assert get_division_for_hp(0) == "Candidate III"
        assert get_division_for_hp(99) == "Candidate III"

    def test_candidate_ii(self):
        assert get_division_for_hp(100) == "Candidate II"
        assert get_division_for_hp(199) == "Candidate II"

    def test_candidate_i(self):
        assert get_division_for_hp(200) == "Candidate I"
        assert get_division_for_hp(299) == "Candidate I"

    def test_member_iii(self):
        assert get_division_for_hp(300) == "Member III"

    def test_inspector_iii(self):
        assert get_division_for_hp(900) == "Inspector III"

    def test_commissioner_i(self):
        assert get_division_for_hp(3667) == "Commissioner I"

    def test_big_brother_iii(self):
        assert get_division_for_hp(4500) == "Big Brother III"

    def test_big_brother_i(self):
        assert get_division_for_hp(7500) == "Big Brother I"
        assert get_division_for_hp(99999) == "Big Brother I"

    def test_negative(self):
        assert get_division_for_hp(-1) == "Candidate III"


class TestGetDivisionForConservative:
    def test_cadence_iii(self):
        assert get_division_for_conservative(0) == "Cadence III"
        assert get_division_for_conservative(199) == "Cadence III"

    def test_cadence_ii(self):
        assert get_division_for_conservative(200) == "Cadence II"

    def test_contender_iii(self):
        assert get_division_for_conservative(600) == "Contender III"

    def test_challenger_i(self):
        assert get_division_for_conservative(1800) == "Challenger I"

    def test_rhythmus_i(self):
        assert get_division_for_conservative(4300) == "Rhythmus I"
        assert get_division_for_conservative(9999) == "Rhythmus I"

    def test_index_ordering(self):
        assert BSK_DIVISION_INDEX["Cadence III"] == 0
        assert BSK_DIVISION_INDEX["Rhythmus I"] == 14
        assert BSK_DIVISION_INDEX["Contender III"] > BSK_DIVISION_INDEX["Cadence I"]

    def test_season_bonus_all_keys_present(self):
        from utils.hp_calculator import HPS_DIVISION_THRESHOLDS
        for _, div in HPS_DIVISION_THRESHOLDS:
            assert div in SEASON_BONUS_HPS, f"{div} missing from SEASON_BONUS_HPS"
