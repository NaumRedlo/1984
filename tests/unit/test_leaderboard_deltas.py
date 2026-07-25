"""Growth maths for the delta leaderboard (services/leaderboard/deltas.py).

The interesting case is hits_per_play: it's a ratio, so the delta of the ratio
is noise (a lifetime average barely moves in a week). What's ranked instead is
the ratio *of the period itself*.
"""

from types import SimpleNamespace

import pytest

from services.leaderboard.deltas import (
    DELTA_CATEGORIES, delta_for, absolute_for, compute_deltas, movement,
)


def _u(uid, *, pp=0, acc=0.0, plays=0, time=0, score=0, hits=0):
    return SimpleNamespace(id=uid, player_pp=pp, accuracy=acc, play_count=plays,
                           play_time=time, ranked_score=score, total_hits=hits)


def test_categories_exclude_removed_top_score():
    assert "best_pp" not in DELTA_CATEGORIES
    assert set(DELTA_CATEGORIES) == {
        "pp", "accuracy", "play_count", "play_time", "ranked_score", "hits_per_play"}


@pytest.mark.parametrize("key, now, anchor, expected", [
    ("pp", _u(1, pp=8214), _u(1, pp=7802), 412),
    ("play_count", _u(1, plays=12_345), _u(1, plays=11_933), 412),
    ("play_time", _u(1, time=30_000), _u(1, time=7_200), 22_800),
    ("ranked_score", _u(1, score=1_045_000), _u(1, score=1_000_000), 45_000),
])
def test_simple_deltas(key, now, anchor, expected):
    assert delta_for(now, anchor, key) == expected


def test_accuracy_delta_is_percentage_points():
    assert round(delta_for(_u(1, acc=98.65), _u(1, acc=98.50), "accuracy"), 2) == 0.15


def test_hits_per_play_is_the_period_ratio_not_lifetime_drift():
    now = _u(1, plays=10_200, hits=4_090_000)
    anchor = _u(1, plays=10_000, hits=4_000_000)
    # 90 000 hits over 200 plays this period.
    assert delta_for(now, anchor, "hits_per_play") == 450.0
    # The lifetime average barely moved — that's why it isn't what we rank.
    assert round(absolute_for(now, "hits_per_play"), 2) == 400.98


def test_no_plays_this_period_is_unrankable_not_zero():
    same = _u(1, plays=10_000, hits=4_000_000)
    assert delta_for(same, same, "hits_per_play") is None


def test_missing_anchor_is_unrankable():
    assert delta_for(_u(1, pp=100), None, "pp") is None


def test_compute_deltas_ranks_and_drops_non_gainers():
    users = [_u(1, pp=8214), _u(2, pp=6000), _u(3, pp=5000)]
    anchors = {1: _u(1, pp=7802), 2: _u(2, pp=6000), 3: _u(3, pp=4797)}
    ranked = compute_deltas(users, anchors, "pp")
    assert [r["user_id"] for r in ranked] == [1, 3]      # #2 gained nothing
    assert ranked[0]["delta"] == 412 and ranked[0]["absolute"] == 8214


def test_compute_deltas_skips_players_without_an_anchor():
    users = [_u(1, pp=100), _u(2, pp=999)]
    ranked = compute_deltas(users, {1: _u(1, pp=50)}, "pp")
    assert [r["user_id"] for r in ranked] == [1]


@pytest.mark.parametrize("pos, prev, expected", [
    (1, {"pp": 5}, 4),      # climbed
    (5, {"pp": 3}, -2),     # dropped
    (3, {"pp": 3}, 0),      # held
    (3, None, None),        # no history at all -> "new"
    (3, {"accuracy": 1}, None),   # no history for THIS category
])
def test_movement(pos, prev, expected):
    assert movement(pos, prev, "pp") == expected


# ── labelling (services/leaderboard/delta_card.py) ────────────────────────

def test_delta_labels_per_category():
    from services.leaderboard.delta_card import format_delta
    assert format_delta("pp", 412, "ru") == "+412 pp"
    assert format_delta("accuracy", 0.15, "ru").startswith("+0.15")
    assert format_delta("play_time", 22_800, "ru") == "+6ч 20м"
    assert format_delta("ranked_score", 45_000_000, "ru") == "+45 000 000"
    # A period ratio is a level, not a gain — it must not carry a "+".
    assert format_delta("hits_per_play", 450.0, "ru") == "450.0"


def test_period_label_matches_the_mockup():
    from services.leaderboard.delta_card import period_label
    assert period_label("2026-W30", "ru") == "неделя 30 · 20–26 июля"
    # A week spanning two months names both.
    assert period_label("2026-W31", "ru") == "неделя 31 · 27 июля – 2 августа"


def test_active_title_resolves_from_the_title_registry():
    """The subtitle is the title the player pinned with `st` — resolved exactly
    like the profile card does, not the legacy User.rank column."""
    from services.leaderboard.delta_card import active_title
    from utils.titles import TITLE_REGISTRY

    code = next(iter(TITLE_REGISTRY))
    label, color = active_title(code, "ru")
    assert label == TITLE_REGISTRY[code].name_for("ru")
    assert color == TITLE_REGISTRY[code].color

    # No title set, or a code that no longer exists -> no subtitle at all, which
    # is what makes the card centre the name against the avatar.
    assert active_title(None, "ru") == ("", None)
    assert active_title("no_such_title_code", "ru") == ("", None)


def test_card_renders_png():
    from services.image.render.leaderboard_delta import render_delta_leaderboard

    def row(pos, name, uid, mv):
        return {"position": pos, "user_id": uid, "username": name,
                "rank_title_label": "Кандидат", "delta_label": "+412 pp",
                "absolute_label": "8 214 всего", "movement": mv, "avatar_data": None}

    png = render_delta_leaderboard({
        "lang": "ru", "title": "Лидерборд · прирост", "subtitle": "неделя 30 · 20–26 июля",
        "meta_right": "PP", "meta_right_sub": "42 участника", "fmt": {"new": "new"},
        "rows": [row(1, "a", 1, 4), row(2, "b", 2, None), row(3, "c", 3, -1)],
        "self_row": {**row(14, "me", 99, 2), "is_self": True, "gap_label": "до 13-го места 24 pp"},
        "footer_left": "1984", "footer_right": "обновлено 26.07",
    })
    assert png[:8] == b"\x89PNG\r\n\x1a\n"

    # Empty board (nobody gained yet) must still render.
    empty = render_delta_leaderboard({
        "lang": "ru", "title": "t", "subtitle": "s", "rows": [], "self_row": None,
        "empty_label": "идёт сбор данных", "fmt": {"new": "new"},
    })
    assert empty[:8] == b"\x89PNG\r\n\x1a\n"
