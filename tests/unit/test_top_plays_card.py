"""Headless render checks for the top-plays card
(services/image/render/top_plays.py)."""

from types import SimpleNamespace
from datetime import datetime, timezone, timedelta

from services.image.core import CardRenderer
from services.image.render.top_plays import build_top_plays_card_data, ROWS_PER_PAGE
from utils.best_scores import build_top_plays_list


def _score(i, pp, **overrides):
    base = dict(
        pp=pp, previous_pp=None, pp_changed_at=None,
        score_id=i, beatmap_id=i, beatmapset_id=1000 + i,
        artist=f"Artist {i}", title=f"Title {i}", version="Extra", creator="mapper",
        mods="HD,HR", star_rating=5.0 + i * 0.1, eff_sr=5.0, accuracy=97.5,
        max_combo=1000 + i, map_max_combo=1000 + i, rank="S", is_fc=True,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _render(data, n_rows=None):
    covers = [None] * (n_rows if n_rows is not None else len(data.get("rows", [])))
    return CardRenderer().generate_top_plays_card(data, None, covers).getvalue()


def test_renders_default_lang_when_missing():
    scores = [_score(i, 300 - i * 10) for i in range(5)]
    built = build_top_plays_list(scores)
    data = build_top_plays_card_data("kazaki1865", "@kazaki", "RU", built)
    assert data["lang"] == "en"
    png = _render(data)
    assert png.startswith(b"\x89PNG") and len(png) > 2000


def test_renders_russian():
    scores = [_score(i, 300 - i * 10) for i in range(5)]
    built = build_top_plays_list(scores)
    data = build_top_plays_card_data("kazaki1865", "@kazaki", "RU", built, lang="ru")
    png = _render(data)
    assert png.startswith(b"\x89PNG") and len(png) > 2000


def test_renders_with_new_and_changed_badges():
    now = datetime.now(timezone.utc)
    scores = [
        _score(0, 300, previous_pp=None, pp_changed_at=now),          # NEW
        _score(1, 250, previous_pp=230, pp_changed_at=now - timedelta(days=2)),  # +20
        _score(2, 200, previous_pp=210, pp_changed_at=now - timedelta(days=1)),  # -10
        _score(3, 150),  # no badge
    ]
    built = build_top_plays_list(scores, now=now)
    data = build_top_plays_card_data("kazaki1865", None, "RU", built, lang="ru")
    png = _render(data)
    assert png.startswith(b"\x89PNG")


def test_renders_empty_state():
    built = build_top_plays_list([])
    data = build_top_plays_card_data("NoScores", None, "US", built)
    png = _render(data)
    assert png.startswith(b"\x89PNG")


def test_long_title_and_artist_do_not_crash():
    scores = [_score(
        0, 300,
        title="An Extremely Long Beatmap Title That Could Break The Layout (TV Size) [Extra Difficulty]",
        artist="A Very Long Artist Name That Might Overflow The Row",
        mods="HD,HR,DT,FL",
    )]
    built = build_top_plays_list(scores)
    data = build_top_plays_card_data("LongUsernameHere", "@longhandle", "US", built)
    png = _render(data)
    assert png.startswith(b"\x89PNG")


def test_pagination_slices_rows():
    scores = [_score(i, 300 - i) for i in range(23)]
    built = build_top_plays_list(scores)
    data = build_top_plays_card_data("u", None, "US", built, page=1)
    assert len(data["rows"]) == ROWS_PER_PAGE
    assert data["rows"][0]["position"] == ROWS_PER_PAGE + 1
    assert data["total_pages"] == 5  # ceil(23/5)


def test_page_clamped_to_valid_range():
    scores = [_score(i, 300 - i) for i in range(3)]
    built = build_top_plays_list(scores)
    data = build_top_plays_card_data("u", None, "US", built, page=99)
    assert data["page"] == 0  # only 1 page exists


def test_pagination_slices_a_larger_list_to_one_page():
    scores = [_score(i, 300 - i * 10) for i in range(12)]
    built = build_top_plays_list(scores)
    data = build_top_plays_card_data("u", None, "US", built, page=0)
    assert len(data["rows"]) == ROWS_PER_PAGE  # page only has 5, out of 12
    assert data["total_pages"] == 3


def test_profile_stats_pass_through_unchanged():
    # 2026-07-04 redesign: the summary strip shows the player's overall
    # profile numbers (same ones /pf shows), not stats derived from this
    # list — build_top_plays_card_data just forwards them.
    scores = [_score(i, 300 - i * 10) for i in range(3)]
    built = build_top_plays_list(scores)
    data = build_top_plays_card_data(
        "u", None, "US", built, global_rank=12345, player_pp=8901.4, accuracy=98.76,
    )
    assert data["global_rank"] == 12345
    assert data["player_pp"] == 8901.4
    assert data["accuracy"] == 98.76
