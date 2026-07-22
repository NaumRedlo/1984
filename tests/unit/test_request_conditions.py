"""Map-request condition matching (services/requests/conditions.py) — pure, no DB."""

from types import SimpleNamespace

from utils.i18n import t
from services.requests.conditions import (
    default_conditions, serialize, parse, parse_mods, format_mods,
    score_meets, describe, play_from_attempt, Play,
)


def _play(passed=True, acc=98.0, combo=500, mods="", rank="S", is_fc=True, miss=0):
    return Play(passed, acc, combo, parse_mods(mods), rank, is_fc, miss)


def test_serialize_parse_roundtrip_fills_defaults():
    cond = parse(serialize({"min_accuracy": 95.0}))
    assert cond["min_accuracy"] == 95.0
    assert cond["pass"] is True and cond["require_fc"] is False
    # malformed / empty -> defaults
    assert parse("not json") == default_conditions()
    assert parse(None) == default_conditions()


def test_parse_mods_variants_and_nc_is_dt():
    assert parse_mods("HDDT") == {"HD", "DT"}
    assert parse_mods("hd dt") == {"HD", "DT"}
    assert parse_mods("HD,HR") == {"HD", "HR"}
    assert parse_mods("-") == frozenset()
    assert parse_mods("NC") == {"DT"}           # nightcore matches DT


def test_format_mods_canonical_order():
    assert format_mods(parse_mods("DTHD")) == "HDDT"
    assert format_mods(parse_mods("HRHD")) == "HDHR"


def test_score_meets_pass_requirement():
    assert score_meets({"pass": True}, _play(passed=True))
    assert not score_meets({"pass": True}, _play(passed=False))
    # "any play" (pass not required) accepts a fail
    assert score_meets({"pass": False}, _play(passed=False))


def test_score_meets_accuracy():
    assert score_meets({"min_accuracy": 95.0}, _play(acc=97.0))
    assert not score_meets({"min_accuracy": 99.0}, _play(acc=98.0))


def test_score_meets_fc_and_combo():
    assert score_meets({"require_fc": True}, _play(is_fc=True))
    assert not score_meets({"require_fc": True}, _play(is_fc=False, miss=3))
    # fallback when is_fc is unknown: no misses = FC
    assert score_meets({"require_fc": True}, _play(is_fc=None, miss=0))
    assert score_meets({"min_combo": 400}, _play(combo=500))
    assert not score_meets({"min_combo": 600}, _play(combo=500))


def test_score_meets_mods_subset_and_rank():
    assert score_meets({"mods": "HDDT"}, _play(mods="HDDTHR"))   # superset ok
    assert not score_meets({"mods": "HDDT"}, _play(mods="HD"))   # missing DT
    assert score_meets({"mods": "DT"}, _play(mods="NC"))         # NC covers DT
    assert score_meets({"min_rank": "S"}, _play(rank="X"))       # X(SS) >= S
    assert score_meets({"min_rank": "S"}, _play(rank="SH"))      # silver S == S
    assert not score_meets({"min_rank": "SS"}, _play(rank="S"))


def test_play_from_attempt_normalizes_accuracy_and_mods():
    att = SimpleNamespace(passed=True, accuracy=0.985, max_combo=500,
                          mods="HD,DT", rank="S", is_fc=True, count_miss=0)
    p = play_from_attempt(att)
    assert abs(p.accuracy - 98.5) < 1e-6      # stored 0–1 -> percent
    assert p.mods == {"HD", "DT"}


def test_describe_is_localized_nonempty():
    d = describe({"pass": True, "min_accuracy": 95.0, "require_fc": True,
                  "mods": "HDDT", "min_rank": "S"}, t, "ru")
    assert "95" in d and "HDDT" in d and d.strip()


def test_wizard_combo_cycle_maps_to_conditions():
    from bot.handlers.requests.wizard import _combo_cycle, _apply_combo, _combo_label
    # With a known map max combo: off → percentages → FC.
    cyc = _combo_cycle(1000)
    assert [label for label, _ in cyc] == ["off", "50%", "75%", "90%", "95%", "FC"]
    cond: dict = {}
    _apply_combo(cond, 0.75, 1000)
    assert cond["min_combo"] == 750 and cond["require_fc"] is False
    _apply_combo(cond, "FC", 1000)
    assert cond["require_fc"] is True and cond["min_combo"] is None
    _apply_combo(cond, None, 1000)
    assert cond["require_fc"] is False and cond["min_combo"] is None
    # Unknown map max combo: only off / FC (no percentages to compute).
    assert [label for label, _ in _combo_cycle(None)] == ["off", "FC"]
    assert _combo_label({"min_combo": 500}, "en") == "≥500"


def test_wizard_custom_accuracy_parsing():
    from bot.handlers.requests.wizard import _parse_acc
    assert _parse_acc("96") == (96, None)          # whole number
    assert _parse_acc("96%") == (96, None)         # trailing % tolerated
    assert _parse_acc("96,5") == (96.5, None)      # comma decimal tolerated
    assert _parse_acc("abc")[1] == "req.custom.bad_number"
    assert _parse_acc("150")[1] == "req.custom.bad_acc"
    assert _parse_acc("-5")[1] == "req.custom.bad_acc"
