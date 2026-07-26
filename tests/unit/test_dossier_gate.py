"""Who may reach the render engine, and what it reports back.

The gate is the point of this module: Dossier runs a native binary and pulls
beatmaps on demand, so "everyone" and even "every admin" are wrong answers
while it's under test.
"""

import os
import types as pytypes

import pytest

from bot.handlers.dossier.handlers import _format
from utils import render_access


@pytest.fixture
def testers(monkeypatch):
    def _set(ids):
        monkeypatch.setattr(render_access, "RENDER_TESTER_IDS", ids)

    return _set


def _event(user_id):
    return pytypes.SimpleNamespace(from_user=pytypes.SimpleNamespace(id=user_id))


def test_nobody_passes_when_the_list_is_empty(testers):
    testers([])
    assert render_access.can_use_render(1) is False
    assert render_access.can_use_render(0) is False


def test_only_listed_ids_pass(testers):
    testers([111, 222])
    assert render_access.can_use_render(111) is True
    assert render_access.can_use_render(333) is False


def test_admins_are_not_automatically_testers(testers, monkeypatch):
    """Running the bot and testing an unfinished simulator are different kinds
    of trust; conflating them would hand the second to everyone with the first."""
    from config import settings

    monkeypatch.setattr(settings, "ADMIN_IDS", [999])
    testers([])
    assert render_access.can_use_render(999) is False


@pytest.mark.asyncio
async def test_filter_admits_testers_and_ignores_everyone_else(testers):
    testers([42])
    gate = render_access.RenderTesterFilter()
    assert await gate(_event(42)) is True
    assert await gate(_event(43)) is False


@pytest.mark.asyncio
async def test_filter_survives_an_event_without_a_user(testers):
    testers([42])
    gate = render_access.RenderTesterFilter()
    assert await gate(pytypes.SimpleNamespace(from_user=None)) is False


# ── the read-out ─────────────────────────────────────────────────────────

def _result(**overrides):
    base = {
        "player": "tester",
        "mods": "HDHR",
        "objects": 1234,
        "exact": True,
        "counts_match": True,
        "combo_match": True,
        "ours": {"300": 1947, "100": 5, "50": 0, "miss": 0},
        "theirs": {"300": 1947, "100": 5, "50": 0, "miss": 0},
        "our_max_combo": 2616,
        "their_max_combo": 2616,
        "our_accuracy": 99.8288,
        "their_accuracy": 99.8288,
    }
    base.update(overrides)
    return base


def test_a_matching_run_says_so():
    text = _format(_result(), "Artist — Title [Insane]")
    assert "Сходится полностью." in text
    assert "Artist — Title [Insane]" in text
    assert "←" not in text, "nothing should be flagged when everything agrees"


def test_disagreeing_rows_are_marked():
    text = _format(
        _result(
            exact=False,
            ours={"300": 1946, "100": 6, "50": 0, "miss": 0},
            our_accuracy=99.76,
        ),
        "Artist — Title [Insane]",
    )
    assert "Расхождение." in text
    # The 300 and 100 rows disagree, and so does accuracy; the 50/miss/combo
    # rows agree and must stay unmarked.
    assert text.count("←") == 3


def test_a_combo_only_mismatch_marks_just_the_combo():
    text = _format(_result(exact=False, our_max_combo=2615), "map")
    assert text.count("←") == 1


# ── telling our misses from the player's ─────────────────────────────────

def _misses(**overrides):
    base = {
        "circle": 0,
        "slider": 0,
        "spinner": 0,
        "with_nearby_click": 0,
        "geometry_suspects": 0,
        "median_overshoot_px": None,
        "spin_rotations": None,
        "spin_required": None,
    }
    base.update(overrides)
    return base


def test_no_misses_adds_nothing():
    assert "промах" not in _format(_result(misses=_misses()), "map").split("<pre>")[0]
    assert _format(_result(), "map").endswith("Сходится полностью.")


def test_a_click_just_outside_the_circle_is_called_our_bug():
    text = _format(
        _result(
            exact=False,
            misses=_misses(circle=5, with_nearby_click=5, geometry_suspects=4, median_overshoot_px=3.2),
        ),
        "map",
    )
    assert "Наши промахи: 5 (круги 5)" in text
    assert "чуть мимо круга на ~3.2 px" in text


def test_misses_with_no_click_nearby_are_credited_to_the_player():
    text = _format(_result(exact=False, misses=_misses(slider=2)), "map")
    assert "слайдеры 2" in text
    assert "промахи игрока" in text


def test_extra_threehundreds_are_sized_against_the_lenient_tails():
    text = _format(
        _result(
            exact=False,
            counts_match=False,
            ours={"300": 2845, "100": 89, "50": 0, "miss": 0},
            theirs={"300": 2825, "100": 109, "50": 0, "miss": 0},
            lenient_tails=57,
            tails_near_the_rim=8,
        ),
        "map",
    )
    assert "Лишних трёхсоток: 20." in text
    assert "по времени: 57, по краю фолловкруга: 8." in text


def test_no_tail_note_when_we_are_not_the_generous_side():
    """The lenience can only explain 300s we handed out and osu! didn't. Saying
    it when the gap runs the other way would send the next look the wrong way."""
    text = _format(
        _result(
            exact=False,
            counts_match=False,
            ours={"300": 100, "100": 20, "50": 0, "miss": 0},
            theirs={"300": 120, "100": 0, "50": 0, "miss": 0},
            lenient_tails=57,
        ),
        "map",
    )
    assert "Хвостов" not in text and "трёхсоток" not in text


def test_the_combo_ceiling_splits_part_counting_from_judgement():
    """The map's published max combo owes nothing to the replay, so it settles
    whether we're miscounting parts or misjudging them — the two call for
    completely different fixes."""
    agree = _format(_result(max_possible_combo=3790), "map", 3790)
    assert "Потолок комбо совпал (3790)" in agree

    disagree = _format(_result(max_possible_combo=3769), "map", 3790)
    assert "у нас 3769, у osu! 3790 (-21)" in disagree
    assert "в числе частей" in disagree


def test_no_ceiling_line_without_an_answer_key():
    # Unranked maps have no published combo; inventing a comparison would be
    # worse than staying quiet.
    assert "Потолок" not in _format(_result(max_possible_combo=3769), "map", None)


def test_failed_spinners_report_rotations_not_clicks():
    """A spinner has no click to blame. Reporting "кликов рядом не было" for
    one would point the investigation at the wrong subsystem."""
    text = _format(
        _result(exact=False, misses=_misses(spinner=4, spin_rotations=12.0, spin_required=20.0)),
        "map",
    )
    assert "спиннеры 4" in text
    assert "12.0 из 20.0 оборотов (60%)" in text
    assert "Кликов рядом не было" not in text


# ── replays kept for rendering ───────────────────────────────────────────

def test_a_remembered_replay_survives_its_handler(tmp_path):
    """Judging happens in a temp dir that vanishes; the render button fires
    minutes later and needs the file to still be there."""
    from bot.handlers.dossier import renders

    source = tmp_path / "replay.osr"
    source.write_bytes(b"osr bytes")
    token = renders.remember(str(source), "Some map")

    source.unlink()  # the handler's temporary directory is gone
    pending = renders.get(token)
    assert pending is not None
    assert open(pending.replay_path, "rb").read() == b"osr bytes"

    renders.forget(token)
    assert renders.get(token) is None


def test_forgetting_takes_the_files_with_it(tmp_path):
    from bot.handlers.dossier import renders

    source = tmp_path / "replay.osr"
    source.write_bytes(b"x")
    token = renders.remember(str(source), "map")
    workdir = renders.get(token).workdir

    renders.forget(token)
    assert not os.path.exists(workdir), "scratch left behind"


def test_the_store_stays_bounded(tmp_path, monkeypatch):
    """Otherwise a session of experiments quietly fills the disk with replays
    nobody will ever render."""
    from bot.handlers.dossier import renders

    monkeypatch.setattr(renders, "_MAX_PENDING", 3)
    source = tmp_path / "replay.osr"
    source.write_bytes(b"x")

    tokens = [renders.remember(str(source), f"map {i}") for i in range(6)]
    alive = [t for t in tokens if renders.get(t)]
    assert len(alive) <= 3
    # The survivors are the newest ones.
    assert tokens[-1] in alive
    for token in tokens:
        renders.forget(token)
