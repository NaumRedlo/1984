"""Pure pp-weighting / delta-classification logic for the top-plays card
(utils/best_scores.py). No DB — plain objects, mirrors how the renderer
consumes this (see services/image/render/top_plays.py)."""

from types import SimpleNamespace
from datetime import datetime, timezone, timedelta

import pytest

from utils.best_scores import build_top_plays_list, total_weighted_pp, WEIGHT_DECAY, MAX_DELTA_AGE


def _score(pp, previous_pp=None, pp_changed_at=None, **overrides):
    base = dict(
        pp=pp, previous_pp=previous_pp, pp_changed_at=pp_changed_at,
        score_id=1, beatmap_id=1, beatmapset_id=1, artist="A", title="T", version="V",
        creator="C", mods="HD,HR", star_rating=5.0, eff_sr=5.0, accuracy=98.0,
        max_combo=100, map_max_combo=100, rank="X", is_fc=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_sorts_by_pp_descending_and_assigns_positions():
    scores = [_score(100), _score(300), _score(200)]
    built = build_top_plays_list(scores)
    assert [r["pp"] for r in built] == [300, 200, 100]
    assert [r["position"] for r in built] == [1, 2, 3]


def test_weight_decays_by_rank():
    scores = [_score(300), _score(200), _score(100)]
    built = build_top_plays_list(scores)
    assert built[0]["weight_pct"] == pytest.approx(100.0)
    assert built[1]["weight_pct"] == pytest.approx(100.0 * WEIGHT_DECAY)
    assert built[2]["weight_pct"] == pytest.approx(100.0 * WEIGHT_DECAY ** 2)
    assert built[0]["weighted_pp"] == pytest.approx(300.0)
    assert built[1]["weighted_pp"] == pytest.approx(200.0 * WEIGHT_DECAY)


def test_total_weighted_pp_sums_all_rows():
    scores = [_score(300), _score(200)]
    built = build_top_plays_list(scores)
    expected = 300.0 + 200.0 * WEIGHT_DECAY
    assert total_weighted_pp(built) == pytest.approx(expected)


def test_new_score_has_no_previous_pp_and_kind_new():
    now = datetime.now(timezone.utc)
    scores = [_score(150, previous_pp=None, pp_changed_at=now)]
    built = build_top_plays_list(scores, now=now)
    delta = built[0]["delta"]
    assert delta.kind == "new"


def test_changed_score_reports_signed_amount():
    now = datetime.now(timezone.utc)
    scores = [_score(150, previous_pp=140, pp_changed_at=now - timedelta(days=1))]
    built = build_top_plays_list(scores, now=now)
    delta = built[0]["delta"]
    assert delta.kind == "changed"
    assert delta.amount == pytest.approx(10.0)


def test_decreased_score_reports_negative_amount():
    now = datetime.now(timezone.utc)
    scores = [_score(140, previous_pp=150, pp_changed_at=now - timedelta(days=1))]
    built = build_top_plays_list(scores, now=now)
    assert built[0]["delta"].amount == pytest.approx(-10.0)


def test_unchanged_score_has_no_delta_badge():
    scores = [_score(150, previous_pp=None, pp_changed_at=None)]
    built = build_top_plays_list(scores)
    assert built[0]["delta"] is None


def test_delta_older_than_max_age_is_suppressed():
    now = datetime.now(timezone.utc)
    scores = [_score(150, previous_pp=140, pp_changed_at=now - MAX_DELTA_AGE - timedelta(days=1))]
    built = build_top_plays_list(scores, now=now)
    assert built[0]["delta"] is None


def test_delta_just_under_max_age_still_shows():
    now = datetime.now(timezone.utc)
    scores = [_score(150, previous_pp=140, pp_changed_at=now - MAX_DELTA_AGE + timedelta(hours=1))]
    built = build_top_plays_list(scores, now=now)
    assert built[0]["delta"] is not None


def test_mods_string_split_into_list():
    built = build_top_plays_list([_score(100, mods="HD,DT")])
    assert built[0]["mods"] == ["HD", "DT"]


def test_empty_mods_string_yields_empty_list():
    built = build_top_plays_list([_score(100, mods="")])
    assert built[0]["mods"] == []


def test_accepts_dicts_as_well_as_objects():
    d = {"pp": 200, "previous_pp": None, "pp_changed_at": None, "mods": "HD",
         "score_id": 1, "beatmap_id": 1, "beatmapset_id": 1, "artist": "A",
         "title": "T", "version": "V", "creator": "C", "star_rating": 5.0,
         "accuracy": 98.0, "max_combo": 100, "rank": "S", "is_fc": True}
    built = build_top_plays_list([d])
    assert built[0]["pp"] == 200
    assert built[0]["position"] == 1


def test_empty_list_returns_empty():
    assert build_top_plays_list([]) == []
    assert total_weighted_pp([]) == 0
