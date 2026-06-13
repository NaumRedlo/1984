"""Characterization tests for services.duel.round_engine hardcore scoring.

Pins the round-decision rules so a refactor can't silently change them — they
mirror the README "Подсчёт раунда (хардкор)" section:
  * a failed map scores no point;
  * NoFail counts as a fail (passing "on NF" scores nothing);
  * among legitimate passers the higher score wins (ties go to player 1);
  * RANKED divides out the ScoreV2 mod multiplier so stacking HR/HD/DT can't win
    a round on the raw score bonus alone; CASUAL compares raw score.
"""
from __future__ import annotations

import pytest

from services.duel.round_engine import _round_ok, _round_score, _decide_round


def _s(score: int, passed: bool = True, mods=()) -> dict:
    return {"score": score, "passed": passed, "mods": list(mods)}


class TestRoundOk:
    def test_clean_pass_counts(self):
        assert _round_ok(_s(100, passed=True)) is True

    def test_fail_does_not_count(self):
        assert _round_ok(_s(100, passed=False)) is False

    def test_nofail_pass_is_treated_as_fail(self):
        assert _round_ok(_s(100, passed=True, mods=["NF"])) is False

    def test_missing_passed_is_fail(self):
        assert _round_ok({"score": 100}) is False

    def test_pass_with_other_mods_counts(self):
        assert _round_ok(_s(100, passed=True, mods=["HD", "HR"])) is True


class TestRoundScore:
    def test_casual_uses_raw_ignoring_mods(self):
        assert _round_score(_s(110_000, mods=["HR"]), "casual") == 110_000

    def test_ranked_divides_out_hr_multiplier(self):
        # scorev2_multiplier(["HR"]) == 1.10
        assert _round_score(_s(110_000, mods=["HR"]), "ranked") == pytest.approx(100_000)

    def test_ranked_no_mods_is_raw(self):
        assert _round_score(_s(100_000, mods=[]), "ranked") == 100_000


class TestDecideRound:
    def test_both_pass_higher_score_wins(self):
        assert _decide_round(_s(200), _s(100)) == 1
        assert _decide_round(_s(100), _s(200)) == 2

    def test_tie_goes_to_player1(self):
        assert _decide_round(_s(100), _s(100)) == 1

    def test_only_passer_takes_the_round(self):
        assert _decide_round(_s(50, passed=True), _s(999, passed=False)) == 1
        assert _decide_round(_s(999, passed=False), _s(50, passed=True)) == 2

    def test_both_fail_is_void(self):
        assert _decide_round(_s(500, passed=False), _s(400, passed=False)) is None

    def test_nofail_passer_loses_to_clean_passer(self):
        # p1 "passed" only via NoFail → counts as a fail; p2 cleared cleanly.
        assert _decide_round(_s(999, passed=True, mods=["NF"]), _s(10, passed=True)) == 2

    def test_ranked_mod_normalisation_can_flip_winner(self):
        p1 = _s(110_000, mods=["HR"])   # ranked-normalised → 100_000
        p2 = _s(105_000, mods=[])       # ranked-normalised → 105_000
        assert _decide_round(p1, p2, "ranked") == 2    # p2 wins on normalised score
        assert _decide_round(p1, p2, "casual") == 1    # p1 wins on raw score
