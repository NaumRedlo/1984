"""Unit tests for bounty_auto_checker._check_conditions JSON-conditions.

Covers the May 2026 additions: max_ur (Metronome) and min_combo_pct (Marathon),
read out of bounty.conditions JSON in addition to the legacy columns.
"""

import json
from types import SimpleNamespace

from tasks.bounty_auto_checker import _check_conditions


def make_bounty(**overrides):
    """Build a duck-typed Bounty stand-in (only the fields _check_conditions reads)."""
    base = dict(
        min_accuracy=None,
        required_mods=None,
        max_misses=None,
        max_combo=0,
        conditions=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def make_score(*, accuracy=1.0, misses=0, mods=None, combo=0):
    return {
        "accuracy": accuracy,
        "max_combo": combo,
        "statistics": {"count_miss": misses},
        "mods": [{"acronym": m} for m in (mods or [])],
    }


# ── Legacy columns still work ──────────────────────────────────────────────

def test_legacy_min_accuracy_pass():
    b = make_bounty(min_accuracy=95.0)
    s = make_score(accuracy=0.98)
    assert _check_conditions(s, b) == ("win", True)


def test_legacy_min_accuracy_fail():
    b = make_bounty(min_accuracy=99.0)
    s = make_score(accuracy=0.95)
    assert _check_conditions(s, b) == ("pending", False)


def test_legacy_required_mods_exact_ok():
    b = make_bounty(required_mods="HD,HR")
    s = make_score(mods=["HD", "HR"])
    assert _check_conditions(s, b) == ("win", True)


def test_legacy_required_mods_missing():
    b = make_bounty(required_mods="HD,HR")
    s = make_score(mods=["HD"])
    assert _check_conditions(s, b) == ("pending", False)


def test_required_mods_extra_difficulty_mod_rejected():
    # HD bounty + player adds DT (a difficulty-altering mod) → must reject.
    # This was the bug a player reported on 2026-05-27: extra mods on a
    # Mod-bounty silently passed because the old check was a subset test.
    b = make_bounty(required_mods="HD")
    s = make_score(mods=["HD", "DT"])
    assert _check_conditions(s, b) == ("pending", False)


def test_required_mods_extra_difficulty_mod_easier_rejected():
    b = make_bounty(required_mods="HD")
    s = make_score(mods=["HD", "EZ"])
    assert _check_conditions(s, b) == ("pending", False)


def test_required_mods_extra_harmless_mod_accepted():
    # NF / SD / PF / CL don't change map difficulty → must pass.
    b = make_bounty(required_mods="HD")
    s = make_score(mods=["HD", "NF"])
    assert _check_conditions(s, b) == ("win", True)


def test_nm_bounty_rejects_any_difficulty_mod():
    # required_mods is empty (NM bounty) → player must use no difficulty mods.
    b = make_bounty(required_mods=None)
    s = make_score(mods=["HD"])
    assert _check_conditions(s, b) == ("pending", False)


def test_nm_bounty_accepts_no_mods():
    b = make_bounty(required_mods=None)
    s = make_score(mods=[])
    assert _check_conditions(s, b) == ("win", True)


def test_nm_bounty_accepts_only_harmless_mods():
    # NF / SD on an NM bounty are fine.
    b = make_bounty(required_mods=None)
    s = make_score(mods=["NF"])
    assert _check_conditions(s, b) == ("win", True)


def test_nm_bounty_empty_string_required_mods():
    # Empty-string required_mods means the same as None — NM.
    b = make_bounty(required_mods="")
    s = make_score(mods=["HD"])
    assert _check_conditions(s, b) == ("pending", False)


# ── JSON: max_ur (Metronome) ───────────────────────────────────────────────

def test_max_ur_met():
    b = make_bounty(conditions=json.dumps({"max_ur": 75}))
    s = make_score()
    assert _check_conditions(s, b, ur_est=50.0) == ("win", True)


def test_max_ur_exceeded():
    b = make_bounty(conditions=json.dumps({"max_ur": 75}))
    s = make_score()
    assert _check_conditions(s, b, ur_est=100.0) == ("pending", False)


def test_max_ur_unknown_fails_safe():
    b = make_bounty(conditions=json.dumps({"max_ur": 75}))
    s = make_score()
    # ur_est=None → auto-checker must download the replay and retry.
    # "ur_needed" signals that all other conditions passed but UR is unknown.
    assert _check_conditions(s, b, ur_est=None) == ("ur_needed", False)


def test_max_ur_with_misses_still_condition_when_met():
    b = make_bounty(conditions=json.dumps({"max_ur": 75}))
    s = make_score(misses=3)
    assert _check_conditions(s, b, ur_est=50.0) == ("condition", True)


# ── JSON: min_combo_pct (Marathon) ─────────────────────────────────────────

def test_min_combo_pct_met():
    b = make_bounty(conditions=json.dumps({"min_combo_pct": 0.8}))
    s = make_score(combo=900)
    # 900/1000 = 90% ≥ 80%
    assert _check_conditions(s, b, beatmap_max_combo=1000) == ("win", True)


def test_min_combo_pct_short():
    b = make_bounty(conditions=json.dumps({"min_combo_pct": 0.8}))
    s = make_score(combo=700)
    # 700/1000 = 70% < 80%
    assert _check_conditions(s, b, beatmap_max_combo=1000) == ("pending", False)


def test_min_combo_pct_no_reference_fails_safe():
    b = make_bounty(conditions=json.dumps({"min_combo_pct": 0.8}))
    s = make_score(combo=900)
    # No beatmap_max_combo provided → cannot validate → pending.
    assert _check_conditions(s, b, beatmap_max_combo=None) == ("pending", False)


def test_min_combo_pct_fallback_to_bounty_max_combo():
    b = make_bounty(conditions=json.dumps({"min_combo_pct": 0.8}), max_combo=1000)
    s = make_score(combo=900)
    assert _check_conditions(s, b) == ("win", True)


# ── JSON: combined conditions ──────────────────────────────────────────────

def test_combined_legacy_and_json_all_pass():
    b = make_bounty(
        min_accuracy=98.0,
        conditions=json.dumps({"max_ur": 80}),
    )
    s = make_score(accuracy=0.99)
    assert _check_conditions(s, b, ur_est=60.0) == ("win", True)


def test_combined_legacy_pass_json_fail():
    b = make_bounty(
        min_accuracy=98.0,
        conditions=json.dumps({"max_ur": 80}),
    )
    s = make_score(accuracy=0.99)
    assert _check_conditions(s, b, ur_est=120.0) == ("pending", False)


# ── conditions blob defensive parsing ──────────────────────────────────────

def test_conditions_invalid_json_is_ignored():
    b = make_bounty(conditions="not-valid-json{{{")
    s = make_score()
    # Should fall back to legacy-only path; with no legacy fields → win.
    assert _check_conditions(s, b) == ("win", True)


def test_conditions_empty_string_is_ignored():
    b = make_bounty(conditions="")
    s = make_score()
    assert _check_conditions(s, b) == ("win", True)


def test_conditions_non_dict_is_ignored():
    b = make_bounty(conditions=json.dumps(["a", "b"]))
    s = make_score()
    assert _check_conditions(s, b) == ("win", True)
