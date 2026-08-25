import os
import types as pytypes

import pytest

from bot.handlers.dossier.handlers import _format, _section_text
from utils import render_access


@pytest.fixture
def testers(monkeypatch):
    """Set the allowlist, with the release switch off.

    Patched on `config.settings` rather than on `render_access`, which is where
    the gate now reads it: the two spellings of "who can render" — a list of
    ids and `*` for everybody — belong in one place, so there is one answer to
    look up and closing the gate again is the same edit in reverse.
    """
    from config import settings

    def _set(ids):
        monkeypatch.setattr(settings, "RENDER_OPEN_TO_ALL", False)
        monkeypatch.setattr(settings, "RENDER_TESTER_IDS", ids)

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
    assert not _section_text("misses", _result(misses=_misses()))
    assert _format(_result(), "map").endswith("Сходится полностью.")


def test_a_click_just_outside_the_circle_is_called_our_bug():
    text = _section_text(
        "misses",
        _result(
            exact=False,
            misses=_misses(circle=5, with_nearby_click=5, geometry_suspects=4, median_overshoot_px=3.2),
        ),
    )
    assert "Наши промахи: 5 (круги 5)" in text
    assert "чуть мимо круга на ~3.2 px" in text


def test_misses_with_no_click_nearby_are_credited_to_the_player():
    text = _section_text("misses", _result(exact=False, misses=_misses(slider=2)))
    assert "слайдеры 2" in text
    assert "промахи игрока" in text


def test_extra_threehundreds_are_sized_against_the_lenient_tails():
    text = _section_text(
        "tails",
        _result(
            exact=False,
            counts_match=False,
            ours={"300": 2845, "100": 89, "50": 0, "miss": 0},
            theirs={"300": 2825, "100": 109, "50": 0, "miss": 0},
            lenient_tails=57,
            tails_near_the_rim=8,
        ),
    )
    assert "Лишних трёхсоток: 20." in text
    assert "по времени: 57, по краю фолловкруга: 8." in text


def test_no_tail_note_when_we_are_not_the_generous_side():
    text = _section_text(
        "tails",
        _result(
            exact=False,
            counts_match=False,
            ours={"300": 100, "100": 20, "50": 0, "miss": 0},
            theirs={"300": 120, "100": 0, "50": 0, "miss": 0},
            lenient_tails=57,
        ),
    )
    assert "Хвостов" not in text and "трёхсоток" not in text


def test_the_combo_ceiling_splits_part_counting_from_judgement():
    agree = _section_text("combo", _result(max_possible_combo=3790, api_max_combo=3790))
    assert "Потолок комбо совпал (3790)" in agree

    disagree = _section_text("combo", _result(max_possible_combo=3769, api_max_combo=3790))
    assert "у нас 3769, у osu! 3790 (-21)" in disagree
    assert "в числе частей" in disagree


def test_no_ceiling_line_without_an_answer_key():
    assert not _section_text("combo", _result(max_possible_combo=3769, api_max_combo=None))


def test_failed_spinners_report_rotations_not_clicks():
    text = _section_text(
        "misses",
        _result(exact=False, misses=_misses(spinner=4, spin_rotations=12.0, spin_required=20.0)),
    )
    assert "спиннеры 4" in text
    assert "12.0 из 20.0 оборотов (60%)" in text
    assert "Кликов рядом не было" not in text


# ── replays kept for rendering ───────────────────────────────────────────

def test_a_remembered_replay_survives_its_handler(tmp_path):
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


def test_the_upload_cap_follows_the_configured_bot_api(monkeypatch):
    from bot.handlers.dossier import handlers

    monkeypatch.setattr(handlers, "TELEGRAM_BOT_API_URL", "")
    assert handlers._max_video_bytes() == 48 * 1024 * 1024

    monkeypatch.setattr(handlers, "TELEGRAM_BOT_API_URL", "http://localhost:8081")
    assert handlers._max_video_bytes() > 1024 * 1024 * 1024
