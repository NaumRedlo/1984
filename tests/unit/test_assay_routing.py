import json
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.osu import assay, pp_calculator


class _Engine:
    def __init__(self, answer=None, returncode=0):
        self.answer = answer if answer is not None else {
            "star_rating": 6.67, "max_combo": 2362, "pp": 353.18,
            "pp_if_unbroken": 388.74, "pp_if_perfect": 460.34,
        }
        self.returncode = returncode
        self.args = None

    async def __call__(self, *args, **kwargs):
        self.args = list(args)
        engine = self

        class _Process:
            returncode = engine.returncode

            async def communicate(self):
                return json.dumps(engine.answer).encode(), b""

        return _Process()

    def flag(self, name):
        if name not in self.args:
            return None
        return self.args[self.args.index(name) + 1]


@pytest.fixture
def map_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(assay, "CACHE_DIR", tmp_path)
    path = tmp_path / "1494828.osu"
    path.write_bytes(b"osu file format v14\n\n[HitObjects]\n" + b"x" * 100)
    return path


async def _download(_beatmap_id):
    return b"osu file format v14\n\n[HitObjects]\n" + b"x" * 100


async def test_a_map_is_downloaded_once_and_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(assay, "CACHE_DIR", tmp_path)
    calls = []

    async def download(beatmap_id):
        calls.append(beatmap_id)
        return b"osu file format v14\n\n[HitObjects]\n" + b"x" * 100

    first = await assay.beatmap_file(1494828, download)
    second = await assay.beatmap_file(1494828, download)
    assert first == second and first.is_file()
    assert calls == [1494828], "the map was fetched twice"


async def test_a_half_written_map_is_never_read(tmp_path, monkeypatch):
    monkeypatch.setattr(assay, "CACHE_DIR", tmp_path)
    await assay.beatmap_file(1494828, _download)
    assert not list(tmp_path.glob("*.part")), "a scratch file was left behind"


async def test_the_engine_is_asked_before_the_port(map_on_disk):
    engine = _Engine()
    with patch("asyncio.create_subprocess_exec", engine):
        out = await pp_calculator.calculate_pp(
            beatmap_id=1494828, mods_str="HDDT", accuracy=97.876,
            combo=2000, misses=1, count_300=1641, count_100=62, count_50=0,
        )
    assert out["pp_current"] == 353.18
    # And the two hypotheticals are the engine's own, not scaled from anything.
    assert out["pp_if_fc"] == 388.74
    assert out["pp_if_ss"] == 460.34


async def test_every_judgement_count_reaches_the_engine_including_the_zeroes(map_on_disk):
    engine = _Engine()
    with patch("asyncio.create_subprocess_exec", engine):
        await pp_calculator.calculate_pp(
            beatmap_id=1494828, mods_str="", accuracy=97.876,
            combo=2000, misses=1, count_300=1641, count_100=62, count_50=0,
        )
    assert engine.flag("--n300") == "1641"
    assert engine.flag("--n100") == "62"
    assert engine.flag("--n50") == "0", "a zero count was dropped"


async def test_the_accuracy_is_always_handed_over(map_on_disk):
    engine = _Engine()
    with patch("asyncio.create_subprocess_exec", engine):
        await pp_calculator.calculate_pp(
            beatmap_id=1494828, mods_str="", accuracy=99.31,
            combo=2362, misses=0, count_300=1700, count_100=4, count_50=0,
        )
    assert engine.flag("--accuracy") == "99.31"


async def test_a_deployment_without_the_engine_still_answers(map_on_disk, monkeypatch):
    async def missing(*_args, **_kwargs):
        raise FileNotFoundError("no engine here")

    reached_the_port = []

    async def _fallback(*_args, **_kwargs):
        reached_the_port.append(True)
        return None

    with patch("asyncio.create_subprocess_exec", missing):
        monkeypatch.setattr(pp_calculator, "_download_osu_file", _fallback)
        out = await pp_calculator.calculate_pp(beatmap_id=1, mods_str="", accuracy=98.0)
    assert out is None
    assert reached_the_port, "the port was never reached"


async def test_a_stable_score_is_read_as_one(map_on_disk):
    engine = _Engine()
    with patch("asyncio.create_subprocess_exec", engine):
        await pp_calculator.calculate_pp(
            beatmap_id=1244293, mods_str="", accuracy=97.37,
            combo=973, misses=2, count_300=1570, count_100=59, count_50=2,
            classic=True, legacy_total_score=35158760,
        )
    assert "--classic" in engine.args
    assert engine.flag("--legacy-total") == "35158760"


async def test_a_lazer_score_is_not_told_it_is_classic(map_on_disk):
    engine = _Engine()
    with patch("asyncio.create_subprocess_exec", engine):
        await pp_calculator.calculate_pp(
            beatmap_id=1494828, mods_str="", accuracy=99.0, combo=100, misses=0,
            count_300=100, count_100=1, count_50=0,
        )
    assert "--classic" not in engine.args
    assert engine.flag("--legacy-total") is None


async def test_what_a_lazer_score_knows_about_itself_reaches_the_engine(map_on_disk):
    engine = _Engine()
    with patch("asyncio.create_subprocess_exec", engine):
        await pp_calculator.calculate_pp(
            beatmap_id=1494828, mods_str="", accuracy=93.2614,
            combo=187, misses=16, count_300=825, count_100=85, count_50=2,
            slider_ends=398, large_tick_misses=0,
        )
    assert engine.flag("--slider-ends") == "398"


async def test_a_classic_score_sends_no_slider_statistics(map_on_disk):
    engine = _Engine()
    with patch("asyncio.create_subprocess_exec", engine):
        await pp_calculator.calculate_pp(
            beatmap_id=1494828, mods_str="", accuracy=97.37,
            combo=973, misses=2, count_300=1570, count_100=59, count_50=2,
            classic=True, legacy_total_score=35158760,
        )
    assert engine.flag("--slider-ends") is None


# ── when the engine is not there ─────────────────────────────────────────


async def test_falling_back_to_the_port_is_said_out_loud(map_on_disk, caplog):
    """The bug this is here to stop: with no engine the bot went on answering,
    from `rosu-pp-py`, whose figures are the ones the engine's own calculator
    was written to replace. Nothing said so. It was found by somebody noticing
    that the pp looked wrong, which is the worst way to find it."""
    import logging

    async def missing(*_args, **_kwargs):
        raise FileNotFoundError

    with caplog.at_level(logging.WARNING), patch("asyncio.create_subprocess_exec", missing):
        await pp_calculator.calculate_pp(beatmap_id=1494828, mods_str="", accuracy=99.0)

    assert "rosu-pp-py" in caplog.text, "the fallback was silent"
    assert "1494828" in caplog.text, "it did not say which map"


async def test_the_engine_answering_says_nothing_about_a_fallback(map_on_disk, caplog):
    """The other half: a warning on every card would be noise nobody reads."""
    import logging

    engine = _Engine()
    with caplog.at_level(logging.WARNING), patch("asyncio.create_subprocess_exec", engine):
        await pp_calculator.calculate_pp(beatmap_id=1494828, mods_str="", accuracy=99.0)
    assert "rosu-pp-py" not in caplog.text


async def test_the_startup_check_runs_a_real_map_through_the_engine():
    """`is_available` asks whether a file is there and executable, which a
    release built for another architecture also is. This asks the engine for an
    answer and reads it."""
    engine = _Engine()
    with patch("asyncio.create_subprocess_exec", engine):
        assert await assay.working() == ""
    assert engine.flag("--map").endswith("tiny.osu")


async def test_the_startup_check_names_the_trouble_rather_than_hiding_it():
    async def missing(*_args, **_kwargs):
        raise FileNotFoundError

    with patch("asyncio.create_subprocess_exec", missing):
        said = await assay.working()
    assert said and "не ответил" in said


async def test_an_engine_that_answers_nonsense_is_not_a_working_engine():
    """A binary that runs and prints JSON without the figures in it is broken
    in a way that `is_available` and a return code of nought both miss."""
    engine = _Engine(answer={"note": "hello"})
    with patch("asyncio.create_subprocess_exec", engine):
        assert await assay.working() != ""
