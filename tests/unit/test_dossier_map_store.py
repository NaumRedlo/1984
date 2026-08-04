"""Which copy of a beatmap the engine ends up rendering from.

Two files can stand for one map. The archive from a mirror carries the song;
the bare `.osu` from osu! carries the notes and nothing else. The engine
prefers a loose file over an archive — hashing one is a read, an `.osz` is an
inflate — so when both are on disk the silent one wins, and a render that
should have had music does not.

`ensure_map` orders the two downloads for that reason. Ordering is not enough
on its own, which is what these pin: the gap opens over *time*, when a map
fetched on a day the mirrors were down is still on disk on a day they are not.
"""

import os

import pytest

from services.dossier import maps as maps_module
from utils.osu import beatmap_osu


class _Client:
    """The API lookup `ensure_map` starts from, and nothing else."""

    async def lookup_beatmap_by_checksum(self, _checksum):
        return {"id": 1, "beatmapset_id": 2}


def _store(monkeypatch, tmp_path, checksum):
    monkeypatch.setattr(beatmap_osu, "BEATMAP_STORE_DIR", str(tmp_path))
    bare = tmp_path / f"{checksum}.osu"
    bare.write_text("osu file format v14\n")
    return bare


@pytest.mark.asyncio
async def test_the_archive_supersedes_a_bare_osu_left_from_a_bad_day(monkeypatch, tmp_path):
    checksum = "a" * 32
    bare = _store(monkeypatch, tmp_path, checksum)

    async def archive_arrives(_set_id):
        return True

    monkeypatch.setattr(maps_module, "download_beatmap", archive_arrives)
    await maps_module.ensure_map(_Client(), checksum)
    assert not os.path.exists(bare), "the silent copy would have kept winning"


@pytest.mark.asyncio
async def test_a_map_only_osu_could_serve_keeps_its_bare_file(monkeypatch, tmp_path):
    """The fallback is still the fallback. When no mirror has the set, the
    loose file is all there is, and removing it would lose the map."""
    checksum = "b" * 32
    bare = _store(monkeypatch, tmp_path, checksum)

    async def no_mirror(_set_id):
        return False

    async def osu_has_it(_id, _checksum):
        return True

    monkeypatch.setattr(maps_module, "download_beatmap", no_mirror)
    monkeypatch.setattr(maps_module, "download_osu", osu_has_it)
    beatmap = await maps_module.ensure_map(_Client(), checksum)

    assert os.path.exists(bare)
    assert beatmap["_no_audio"] is True, "a silent render has to say so out loud"
