import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import config.settings as settings
from services.dossier import shared

def _a_replay(tmp_path, body: bytes = b"osr bytes"):
    path = tmp_path / "replay.osr"
    path.write_bytes(body)
    return str(path)

def _collecting(monkeypatch, where):
    monkeypatch.setattr(settings, "SHARED_REPLAY_DIR", str(where))
    monkeypatch.setattr(shared, "SHARED_REPLAY_DIR", str(where))

def test_nothing_is_kept_unless_the_host_asked_to_collect(tmp_path, monkeypatch):
    monkeypatch.setattr(shared, "SHARED_REPLAY_DIR", "")
    assert not shared.enabled()
    assert shared.keep(_a_replay(tmp_path), {"exact": True}) is None

def test_a_shared_replay_is_kept_with_the_engines_reading_of_it(tmp_path, monkeypatch):
    store = tmp_path / "store"
    _collecting(monkeypatch, store)

    landed = shared.keep(_a_replay(tmp_path), {"exact": False, "theirs": {"300": 100}})
    assert landed and os.path.isfile(landed)
    assert landed.endswith(".osr")

    beside = landed[: -len(".osr")] + ".json"
    assert os.path.isfile(beside), "the engine's reading is half of what was promised"
    assert json.load(open(beside))["theirs"] == {"300": 100}

def test_the_same_replay_rendered_twice_is_kept_once(tmp_path, monkeypatch):
    store = tmp_path / "store"
    _collecting(monkeypatch, store)

    first = shared.keep(_a_replay(tmp_path), None)
    second = shared.keep(_a_replay(tmp_path), None)
    assert first == second

    kept = [f for _, _, files in os.walk(store) for f in files if f.endswith(".osr")]
    assert len(kept) == 1, f"the same bytes were kept {len(kept)} times"

def test_two_different_replays_are_two_files(tmp_path, monkeypatch):
    store = tmp_path / "store"
    _collecting(monkeypatch, store)

    shared.keep(_a_replay(tmp_path, b"one"), None)
    shared.keep(_a_replay(tmp_path, b"another"), None)
    kept = [f for _, _, files in os.walk(store) for f in files if f.endswith(".osr")]
    assert len(kept) == 2

def test_a_render_is_not_failed_by_a_disk_that_will_not_take_a_copy(tmp_path, monkeypatch):
    _collecting(monkeypatch, tmp_path / "store")

    def refuse(*_a, **_kw):
        raise OSError("no space left on device")

    monkeypatch.setattr(shared.shutil, "copy2", refuse)
    assert shared.keep(_a_replay(tmp_path), None) is None

def test_the_replay_is_kept_only_for_somebody_who_asked(monkeypatch):
    import inspect

    from bot.handlers.dossier import handlers

    source = inspect.getsource(handlers._keep_if_shared)
    assert "user.share_replays" in source
    assert "shared.enabled()" in source

def test_nothing_beyond_the_two_promised_things_is_written(tmp_path, monkeypatch):
    import inspect

    signature = inspect.signature(shared.keep)
    assert list(signature.parameters) == ["replay_path", "verdict"], (
        f"`keep` grew a parameter nobody consented to: {list(signature.parameters)}"
    )

def test_the_count_is_nought_when_nothing_is_collected(monkeypatch):
    monkeypatch.setattr(shared, "SHARED_REPLAY_DIR", "")
    assert shared.how_many() == 0

def test_the_count_is_what_is_actually_held(tmp_path, monkeypatch):
    store = tmp_path / "store"
    _collecting(monkeypatch, store)
    assert shared.how_many() == 0

    shared.keep(_a_replay(tmp_path, b"one"), None)
    shared.keep(_a_replay(tmp_path, b"two"), None)
    shared.keep(_a_replay(tmp_path, b"two"), None)
    assert shared.how_many() == 2

def test_the_bot_says_at_startup_whether_it_is_collecting():
    import inspect

    from bot import main

    source = inspect.getsource(main)
    assert "Shared replays: collecting into" in source
    assert "SHARED_REPLAY_DIR is unset" in source
