"""The bot asking its own engine, instead of ppy and a third-party port.

`dossier assay` is graded against ppy's own answers on a corpus that lives with
it, so what is tested here is not the arithmetic — that is the crate's job — but
the wiring: that the engine is asked first, that it is asked the right thing,
and that a deployment without it still draws a card.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.osu import assay, pp_calculator


class _Engine:
    """Stands in for the binary, recording the command line it was given."""

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
        """What was passed for `name`, or None if it was not passed at all."""
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
    # The engine is another process and takes a path, so the file has to be on
    # disk rather than in memory. A beatmap id names one immutable file, so
    # nothing here expires.
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
    # Two cards for the same beatmap can be drawn at once, so the file appears
    # whole or not at all.
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
    """A zero among the counts is a fact about the play, not an absence.

    Passing them with `or None` turned a play with no fifties into a play whose
    counts were unknown, so the engine solved for a perfect one instead: a 353pp
    score came back as 422, and "if unbroken" and "if perfect" came back equal.
    """
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
    # The engine believes it rather than deriving it, because under lazer's
    # rules accuracy is not derivable from the four judgements — slider tails
    # and large ticks count towards it.
    engine = _Engine()
    with patch("asyncio.create_subprocess_exec", engine):
        await pp_calculator.calculate_pp(
            beatmap_id=1494828, mods_str="", accuracy=99.31,
            combo=2362, misses=0, count_300=1700, count_100=4, count_50=0,
        )
    assert engine.flag("--accuracy") == "99.31"


async def test_a_deployment_without_the_engine_still_answers(map_on_disk, monkeypatch):
    # The binary is built separately from the bot, so a deployment can be half
    # done. rosu stays behind this for exactly that.
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
    """The difference between 263pp and 243, on a real play.

    A stable score records neither the slider ends it dropped nor the ticks it
    missed. Read as though it were a lazer score, its combo losses vanish: the
    play this was found on broke at 973 of 2354 with two misses, and the lazer
    reading calls that exactly two breaks and prices it at 263 where the game
    says 243. The old scoring left a ScoreV1 total instead, and the breaks are
    read back out of that.
    """
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
    # The two readings are not interchangeable in either direction: a lazer
    # score knows what it dropped, and guessing at it instead would throw that
    # away.
    engine = _Engine()
    with patch("asyncio.create_subprocess_exec", engine):
        await pp_calculator.calculate_pp(
            beatmap_id=1494828, mods_str="", accuracy=99.0, combo=100, misses=0,
            count_300=100, count_100=1, count_50=0,
        )
    assert "--classic" not in engine.args
    assert engine.flag("--legacy-total") is None
