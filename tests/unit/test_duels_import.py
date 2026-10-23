import hashlib
import io
import json
import zipfile

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import db.models
from db.database import Base
from db.models.duel_catalog import DuelMapAnalysis, DuelMapAssignment, DuelMapRevision
from services.duels import importer, mirrors

HEAD = """osu file format v14

[General]
Mode: 0

[Metadata]
Creator:Meg
BeatmapID:{beatmap_id}

[Difficulty]
HPDrainRate:5
CircleSize:4
OverallDifficulty:8
ApproachRate:9
SliderMultiplier:1.4
SliderTickRate:1

[TimingPoints]
0,250,4,2,0,60,1,0

[HitObjects]
"""

def _map(beatmap_id: int, count: int = 40) -> bytes:
    objects = [f"{100 + (37 * i) % 300},{100 + (53 * i) % 200},{1000 + 120 * i},1,0,0:0:0:0:" for i in range(count)]
    return (HEAD.format(beatmap_id=beatmap_id) + "\n".join(objects) + "\n").encode()

def _archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, raw in files.items():
            bundle.writestr(name, raw)
    return buffer.getvalue()

def _set(set_id: int, maps: list[tuple[int, float, str, int, bytes]]) -> dict:
    return {"id": set_id, "artist": "Artist", "title": f"Song {set_id}", "creator": "Meg", "status": "ranked",
            "beatmaps": [{"id": beatmap_id, "difficulty_rating": stars, "mode": mode, "status": "ranked", "version": "Extra",
                          "total_length": length, "max_combo": 40, "checksum": hashlib.md5(raw).hexdigest(), "convert": False}
                         for beatmap_id, stars, mode, length, raw in maps]}

class Osu:
    def __init__(self):
        self.asked = []

    async def get_beatmap_attributes(self, beatmap_id, mods=None, ruleset=0):
        self.asked.append((beatmap_id, mods))
        return {"star_rating": 5.0 + {8: 0.1, 16: 0.4, 64: 1.5}[mods]}

@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'import.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

@pytest.fixture(autouse=True)
def rested():
    mirrors.resting.clear()
    yield
    mirrors.resting.clear()

def test_search_keeps_standard_maps_in_range_and_only_ranked_ones_are_filed():
    raw = _map(1)
    body = json.dumps([_set(10, [(1, 5.5, "osu", 120, raw), (2, 3.0, "osu", 120, raw), (3, 5.5, "mania", 120, raw)]),
                       {**_set(11, [(4, 6.0, "osu", 120, raw)]), "status": "loved",
                        "beatmaps": [{**_set(11, [(4, 6.0, "osu", 120, raw)])["beatmaps"][0], "status": "loved"}]}]).encode()
    found = mirrors.read_search(body, 4.0, 8.0)
    assert [entry["beatmap_id"] for entry in found] == [1, 4]
    assert [entry["beatmap_id"] for entry in found if importer.suitable(entry)] == [1]
    assert mirrors.read_search(b"not json", 4.0, 8.0) == []

async def test_a_failing_mirror_rests_and_the_next_one_answers():
    asked = []
    raw = _map(7)

    async def fetch(address):
        asked.append(address)
        if "osu.direct" in address:
            raise OSError("down")
        if "catboy.best/osu/" in address:
            return b"<html>blocked</html>"
        if "catboy.best/d/" in address:
            return None
        return _archive({"a.osu": raw})

    assert await mirrors.fetch_osu(fetch, 70, 7, hashlib.md5(raw).hexdigest(), clock=lambda: 0.0) == ("beatconnect.io", raw)
    asked.clear()
    assert await mirrors.fetch_osu(fetch, 70, 7, None, clock=lambda: 1.0) == ("beatconnect.io", raw)
    assert all("osu.direct" not in address for address in asked)
    assert all("osu.ppy.sh" not in address for address in asked)
    asked.clear()
    await mirrors.fetch_osu(fetch, 70, 7, None, clock=lambda: mirrors.REST + 1)
    assert any("osu.direct" in address for address in asked)

async def test_discovery_files_ranked_maps_with_stars_for_every_slot_mod(factory, tmp_path):
    good, short = _map(1), _map(2)
    sets = [_set(10, [(1, 5.5, "osu", 150, good), (2, 5.6, "osu", 30, short)])]

    async def fetch(address):
        if "search" in address:
            return json.dumps(sets if "offset=0" in address else []).encode()
        if "osu.direct/api/osu/1" in address:
            return good
        return None

    osu = Osu()
    seen = []
    counted = await importer.discover(factory, fetch, osu, str(tmp_path / "osu"), 4.0, 8.0, pages=3, amount=1, pause=0, report=seen.append)
    assert counted == {"unassigned": 1}
    async with factory() as session:
        revision = await session.scalar(select(DuelMapRevision))
        analyses = {row.mods: row.stars for row in (await session.scalars(select(DuelMapAnalysis))).all()}
        assignment = await session.get(DuelMapAssignment, 1)
    assert (revision.beatmap_id, revision.status, revision.source, revision.max_combo) == (1, "ok", "osu.direct", 40)
    assert analyses == {"NF": 5.5, "NFDT": 6.5, "NFHD": 5.1, "NFHR": 5.4}
    assert (assignment.slot, assignment.state, assignment.admitted) == (None, "UNASSIGNED", True)
    assert sorted(mods for _, mods in osu.asked) == [8, 16, 64]
    assert (tmp_path / "osu" / f"{hashlib.md5(good).hexdigest()}.osu").exists()
    again = await importer.discover(factory, fetch, osu, str(tmp_path / "osu"), 4.0, 8.0, pages=1, amount=1, pause=0, report=seen.append)
    assert again == {"known": 1}

async def test_a_published_model_proposes_a_slot_on_import(factory, tmp_path):
    from services.duels.slot_model import Prediction

    raw = _map(5)

    class Model:
        version = "slots-test"

        def predict(self, features, base_ar, hr_cs):
            assert features["mods"] == "NF" and base_ar == 9
            return Prediction("NM1", 0.9, 0.5, {"NM1": 0.9}, False)

    async def fetch(address):
        return raw if "/osu/5" in address else None

    entry = {"beatmap_id": 5, "beatmapset_id": 50, "checksum": hashlib.md5(raw).hexdigest(), "stars": 5.0, "status": "ranked",
             "mapper": "Meg", "length": 100, "max_combo": 40}
    assert await importer.ingest(factory, fetch, Osu(), entry, str(tmp_path), Model()) == "proposed NM1"
    async with factory() as session:
        assignment = await session.get(DuelMapAssignment, 5)
    assert (assignment.slot, assignment.state) == ("NM1", "AUTO_ASSIGNED")

async def test_a_broken_file_is_quarantined_not_offered(factory, tmp_path):
    raw = b"osu file format v14\n\n[General]\nMode: 0\n\n[Metadata]\nBeatmapID:6\n"

    async def fetch(address):
        return raw if "/osu/6" in address else None

    entry = {"beatmap_id": 6, "beatmapset_id": 60, "checksum": None, "stars": 5.0, "status": "ranked", "length": 100}
    assert await importer.ingest(factory, fetch, Osu(), entry, str(tmp_path)) == "parse_failed"
    async with factory() as session:
        revision = await session.scalar(select(DuelMapRevision))
        assignment = await session.get(DuelMapAssignment, 6)
    assert revision.status == "quarantined" and assignment.admitted is False
