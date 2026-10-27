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
from services.duels import importer, mirrors, training

HEAD = """osu file format v14

[General]
Mode: 0

[Metadata]
Title:Example Song
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

async def test_an_old_map_without_its_id_is_checked_by_the_mirror_checksum():
    old = b"osu file format v8\n\n[General]\nAudioFilename: a.mp3\n"
    other = b"osu file format v8\n\n[General]\nAudioFilename: b.mp3\n"
    asked = []

    async def fetch(address):
        asked.append(address)
        if "api/v2/b/58970" in address:
            return json.dumps({"id": 58970, "beatmapset_id": 16457, "checksum": hashlib.md5(old).hexdigest()}).encode()
        if "osu.direct/api/osu/58970" in address:
            return other
        if "catboy.best/osu/58970" in address:
            return old
        return None

    assert await mirrors.fetch_osu(fetch, 0, 58970, None, clock=lambda: mirrors.REST * 10) == ("catboy.best", old)

    async def gone(address):
        if "api/v2/b/" in address:
            return json.dumps({"id": 4384670, "beatmapset_id": 2091789, "checksum": "f" * 32}).encode()
        if "/d/2091789" in address:
            return _archive({"a.osu": _map(4478118)})
        return None

    assert await mirrors.fetch_osu(gone, 0, 4384670, None, clock=lambda: mirrors.REST * 10) is None

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
    assert (revision.title, revision.artist, revision.version) == ("Song 10", "Artist", "Extra")
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

        def predict(self, features, base_ar, hr_cs, views=None):
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

def _described(beatmap_id: int, raw: bytes, status: str = "graveyard", mode="osu", length: int = 120, stars: float = 5.2) -> bytes:
    return json.dumps({"id": beatmap_id, "beatmapset_id": beatmap_id * 10, "mode": mode, "status": status, "total_length": length,
                       "difficulty_rating": stars, "version": "Lonely", "max_combo": 40,
                       "beatmapset": {"title": "Graveyard Song", "artist": "Artist", "creator": "Meg", "status": status}}).encode()

async def test_one_map_is_described_by_the_mirror_and_checked_before_import(factory, tmp_path):
    raw = _map(9)
    answers = {"api/v2/b/9": _described(9, raw), "/osu/9": raw, "api/v2/b/10": _described(10, raw, mode="mania"),
               "api/v2/b/11": _described(11, raw, length=30)}

    async def fetch(address):
        return next((body for part, body in answers.items() if part in address), None)

    outcome, entry = await importer.ingest_one(factory, fetch, Osu(), 9, str(tmp_path))
    assert (outcome, entry["beatmapset_id"], entry["status"], entry["title"]) == ("unassigned", 90, "graveyard", "Graveyard Song")
    assert (await importer.ingest_one(factory, fetch, Osu(), 10, str(tmp_path)))[0] == "not_standard"
    assert (await importer.ingest_one(factory, fetch, Osu(), 11, str(tmp_path)))[0] == "length"
    assert await importer.ingest_one(factory, fetch, Osu(), 12, str(tmp_path)) == ("not_found", None)
    async with factory() as session:
        revision = await session.scalar(select(DuelMapRevision))
    assert (revision.beatmap_id, revision.ranked_status, revision.status) == (9, "graveyard", "ok")

async def test_a_reviewer_adds_one_map_and_an_unranked_one_plays_only_with_a_person_s_slot(factory, tmp_path):
    from db.models.duel import DuelAudit, DuelRole
    from db.models.player import Player
    from services.duels import balance, catalog

    first, second = _map(21), _map(22)
    easy = _map(24)
    answers = {"api/v2/b/21": _described(21, first), "/osu/21": first, "api/v2/b/22": _described(22, second, status="loved"), "/osu/22": second,
               "api/v2/b/24": _described(24, easy, stars=3.0), "/osu/24": easy}

    async def fetch(address):
        return next((body for part, body in answers.items() if part in address), None)

    brought = []

    async def bring(beatmap_id):
        brought.append(beatmap_id)
        return await importer.ingest_one(factory, fetch, Osu(), beatmap_id, str(tmp_path))

    async with factory() as session:
        session.add_all([Player(id=1, osu_user_id=101, osu_username="one"), Player(id=2, osu_user_id=102, osu_username="two")])
        await session.flush()
        session.add(DuelRole(player_id=1, role=balance.BALANCE_REVIEWER))
        await session.commit()
    with pytest.raises(balance.Forbidden):
        await balance.add_map(factory, 2, 21, "NM2", "not a reviewer", (), bring)
    with pytest.raises(catalog.CatalogRefusal) as unknown:
        await balance.add_map(factory, 1, 21, "XX9", "no such slot", (), bring)
    assert unknown.value.code == "UNKNOWN_SLOT" and brought == []
    with pytest.raises(catalog.CatalogRefusal) as missing:
        await balance.add_map(factory, 1, 23, None, "not on any mirror", (), bring)
    assert missing.value.code == "MAP_NOT_FOUND"
    assert await balance.add_map(factory, 1, 21, "NM2", "a fine graveyard aim map", (), bring) == {"outcome": "unassigned", "status": "graveyard"}
    assert await balance.add_map(factory, 1, 22, None, "let Kamerton look", (), bring) == {"outcome": "unassigned", "status": "loved"}
    async with factory() as session:
        rows = {row.beatmap_id: row for row in (await session.scalars(select(DuelMapAssignment))).all()}
        revisions = {row.beatmap_id: row for row in (await session.scalars(select(DuelMapRevision))).all()}
        audit = [(row.action, row.target) for row in (await session.scalars(select(DuelAudit).order_by(DuelAudit.id))).all()]
    assert (rows[21].slot, rows[21].state) == ("NM2", catalog.HUMAN_APPROVED)
    assert catalog.playable(rows[21], revisions[21]) == "human"
    assert (rows[22].slot, rows[22].state) == (None, catalog.UNASSIGNED)
    rows[22].slot, rows[22].state, rows[22].confidence, rows[22].margin = "NM1", catalog.AUTO_ASSIGNED, 0.95, 0.5
    assert catalog.playable(rows[22], revisions[22]) is None, "a model alone never plays a map that is not ranked"
    assert audit == [("map_add", "21"), ("map_approve", "21"), ("map_add", "22")]
    assert await balance.add_map(factory, 1, 21, "NM3", "moved after a second look", (), bring) == {"outcome": "unassigned", "status": "graveyard"}
    async with factory() as session:
        assert (await session.get(DuelMapAssignment, 21)).slot == "NM3"
    await balance.review_map(factory, 1, 22, "REJECT", "not a duel map")
    with pytest.raises(catalog.CatalogRefusal) as stays:
        await balance.add_map(factory, 1, 22, "NM1", "try again", (), bring)
    assert stays.value.code == "REJECTED_STAYS"
    async with factory() as session:
        audit = [row.action for row in (await session.scalars(select(DuelAudit).order_by(DuelAudit.id))).all()]
    assert audit[-1] == "map_reject", "a refused addition leaves no trace in the journal"
    with pytest.raises(catalog.CatalogRefusal) as easy_nm:
        await balance.add_map(factory, 1, 24, "NM1", "too easy without mods", (), bring)
    assert easy_nm.value.code == "STARS_RANGE"
    assert (await balance.add_map(factory, 1, 24, "DT1", "with DT it is 6.5", (), bring))["outcome"] in ("known", "unassigned")
    async with factory() as session:
        assert (await session.get(DuelMapAssignment, 24)).slot == "DT1"

def test_the_catalogue_holds_five_to_ten_stars_and_nothing_else():
    from services.duels.rules import in_catalogue
    assert [in_catalogue(stars) for stars in (4.99, 5.0, 7.3, 10.0, 10.01, None)] == [False, True, True, True, False, False]

WIKI_PAGE = """# osu! World Cup 2023

## Mappools

### Grand Finals

**[Download the mappack here](https://packs.ppy.sh/P231.zip)**

- No Mod
  1. [A - B (C) \\[D\\]](https://osu.ppy.sh/beatmapsets/10#osu/1)
  2. [A - B (C) \\[E\\]](https://osu.ppy.sh/beatmapsets/20#osu/2)
  5. [A - B (C) \\[F\\]](https://osu.ppy.sh/beatmapsets/30#osu/3)
- Hidden
  2. [A - B (C) \\[G\\]](https://osu.ppy.sh/beatmapsets/40#osu/4)
- Flashlight
  1. [A - B (C) \\[H\\]](https://osu.ppy.sh/beatmapsets/50#osu/5)
- Tiebreaker
  1. **[A - B (C) \\[I\\]](https://osu.ppy.sh/beatmapsets/60#osu/6)**

## Match results

- No Mod
  1. [A - B (C) \\[X\\]](https://osu.ppy.sh/beatmapsets/70#osu/7)
"""

def test_wiki_pools_become_slots_and_foreign_slots_become_negatives():
    from services.duels import corpus

    found = corpus.read_wiki(WIKI_PAGE, 2023, "wiki")
    assert [(e.stage, e.original_slot, e.beatmap_id, e.beatmapset_id) for e in found] == [
        ("grand finals", "NM1", 1, 10), ("grand finals", "NM2", 2, 20), ("grand finals", "NM5", 3, 30),
        ("grand finals", "HD2", 4, 40), ("grand finals", "FL1", 5, 50), ("grand finals", "TB", 6, 60)]
    assert [corpus.label(e.original_slot) for e in found] == ["NM1", "NM2", "NM5", "HD2", "FL1", "TB"]

async def test_the_corpus_is_filed_apart_from_the_catalogue_and_trains_a_shadow_model(factory, tmp_path):
    from db.models.duel import DuelModelVersion

    files = {i: _map(i, 30 + 5 * i) for i in range(1, 7)}

    async def fetch(address):
        if "osu-wiki" in address:
            return WIKI_PAGE.replace("2023", "2021").encode() if "/2021/" in address else None
        for number, raw in files.items():
            if address.endswith(f"/osu/{number}"):
                return raw
        return None

    counted = await importer.load_corpus(factory, fetch, str(tmp_path), years=(2021,), pause=0, report=lambda text: None)
    assert counted == {"filed": 6}
    async with factory() as session:
        examples = (await session.scalars(select(db.models.duel_catalog.DuelTrainingExample))).all()
        assignments = (await session.scalars(select(DuelMapAssignment))).all()
        analyses = (await session.scalars(select(DuelMapAnalysis))).all()
    assert {e.original_slot: (e.normalized_label, e.mods) for e in examples}["HD2"] == ("HD2", "NFHD")
    assert assignments == [] and len(analyses) == 6 * len(importer.ANALYSED)
    again = await importer.load_corpus(factory, fetch, str(tmp_path), years=(2021,), pause=0, report=lambda text: None)
    assert again == {"filed": 6}
    rows = await importer.corpus_rows(factory)
    assert sorted(row[1] for row in rows) == ["FL1", "HD2", "NM1", "NM2", "NM5", "TB"]
    from services.duels.slot_model import vector
    hidden = next(row for row in rows if row[1] == "HD2")
    async with factory() as session:
        example = next(e for e in (await session.scalars(select(db.models.duel_catalog.DuelTrainingExample))).all() if e.original_slot == "HD2")
        plain = await session.scalar(select(DuelMapAnalysis).where(DuelMapAnalysis.revision_hash == example.revision_hash, DuelMapAnalysis.mods == "NF"))
    assert hidden[0][:len(vector(plain.features))] == vector(plain.features) and len(hidden[0]) > len(vector(plain.features))
    version, found = await training.train(factory, steps=20)
    assert version == "kamerton-1" and found["maps"] == 6 and found["test"]["count"] == 0 and found["years"] == {}
    async with factory() as session:
        assert (await session.get(DuelModelVersion, version)).state == "shadow"
    entry = {"beatmap_id": 1, "beatmapset_id": 10, "checksum": hashlib.md5(files[1]).hexdigest(), "stars": 5.0, "status": "ranked",
             "mapper": "Meg", "length": 100, "max_combo": 30}
    assert await importer.ingest(factory, fetch, Osu(), entry, str(tmp_path)) == "unassigned"
    async with factory() as session:
        assignment = await session.get(DuelMapAssignment, 1)
        revision = await session.scalar(select(DuelMapRevision).where(DuelMapRevision.beatmap_id == 1))
        stars = await session.scalar(select(DuelMapAnalysis.stars).where(DuelMapAnalysis.revision_hash == revision.hash,
                                                                         DuelMapAnalysis.mods == "NFDT"))
    assert assignment.admitted and revision.ranked_status == "ranked" and stars == 6.5

async def test_a_published_model_sorts_waiting_catalogue_maps(factory, tmp_path):
    from services.duels.slot_model import Prediction

    raws = {7: _map(7), 8: _map(8, 50)}

    async def fetch(address):
        for number, raw in raws.items():
            if address.endswith(f"/osu/{number}"):
                return raw
        return None

    for number, raw in raws.items():
        entry = {"beatmap_id": number, "beatmapset_id": number * 10, "checksum": hashlib.md5(raw).hexdigest(), "stars": 5.0,
                 "status": "ranked", "mapper": "Meg", "length": 100, "max_combo": 40}
        assert await importer.ingest(factory, fetch, Osu(), entry, str(tmp_path)) == "unassigned"

    class Model:
        version = "slots-test"

        def predict(self, features, base_ar, hr_cs, views=None):
            assert base_ar == 9 and hr_cs == pytest.approx(5.2)
            sure = features["objects"] == 40
            return Prediction("DT1" if sure else None, 0.9 if sure else 0.4, 0.5 if sure else 0.05, {}, False)

    assert await importer.reassess(factory, Model()) == {"proposed DT1": 1, "below_threshold": 1}
    async with factory() as session:
        assert (await session.get(DuelMapAssignment, 7)).slot == "DT1"
        assert (await session.get(DuelMapAssignment, 8)).slot is None

async def test_reviewers_map_owc_cells_and_their_own_pools_and_training_follows(factory, tmp_path):
    from db.models.duel import DuelAudit, DuelRole
    from db.models.player import Player
    from services.duels import balance, corpus
    from services.duels.errors import DuelError

    files = {i: _map(i, 30 + 5 * i) for i in range(1, 12)}

    async def fetch(address):
        if "osu-wiki" in address:
            return WIKI_PAGE.replace("2023", "2021").encode() if "/2021/" in address else None
        for number, raw in files.items():
            if address.endswith(f"/osu/{number}"):
                return raw
        return None

    async with factory() as session:
        session.add_all([Player(id=1, osu_user_id=101, osu_username="naum"), Player(id=2, osu_user_id=102, osu_username="guest")])
        await session.flush()
        session.add(DuelRole(player_id=1, role=balance.BALANCE_REVIEWER))
        await session.commit()
    await importer.load_corpus(factory, fetch, str(tmp_path), years=(2021,), pause=0, report=lambda text: None)
    with pytest.raises(balance.Forbidden):
        await balance.corpus_table(factory, 2)
    cells = await balance.corpus_table(factory, 1)
    assert [(c["slot"], c["maps"][0]["label"], c["maps"][0]["verified"]) for c in cells if c["slot"] in ("NM2", "NM5")] == [("NM2", "NM2", False), ("NM5", "NM5", False)]
    assert all(m["title"] for c in cells for m in c["maps"] if m["file"])
    from sqlalchemy import update
    from db.models.duel_catalog import DuelMapRevision
    async with factory() as session:
        await session.execute(update(DuelMapRevision).values(title=None))
        await session.commit()
    await importer.load_corpus(factory, fetch, str(tmp_path), years=(2021,), pause=0, report=lambda text: None)
    assert all(m["title"] == "Example Song" for c in await balance.corpus_table(factory, 1) for m in c["maps"] if m["file"])
    await balance.label_corpus(factory, 1, corpus.CORPUS_VERSION, 2021, "grand finals", "NM2", None, "NM3", "в этом году NM2 был альтернированием")
    await balance.label_corpus(factory, 1, corpus.CORPUS_VERSION, 2021, "grand finals", "NM5", 3, "NM4", "техника")
    await balance.label_corpus(factory, 1, corpus.CORPUS_VERSION, 2021, "grand finals", "NM1", 1, corpus.EXCLUDED, "спорная карта")
    with pytest.raises(DuelError):
        await balance.label_corpus(factory, 1, corpus.CORPUS_VERSION, 2021, "grand finals", "NM1", 1, "XX9", "нет такого")
    rows = await importer.corpus_rows(factory)
    assert sorted((row[1], row[5]) for row in rows) == [("FL1", False), ("HD2", False), ("NM3", True), ("NM4", True), ("TB", False)]
    await balance.label_corpus(factory, 1, corpus.CORPUS_VERSION, 2021, "grand finals", "NM5", 3, "OTHER", "подтвердил как было")
    rows = await importer.corpus_rows(factory)
    assert ("NM5", True) in [(row[1], row[5]) for row in rows]
    await balance.label_corpus(factory, 1, corpus.CORPUS_VERSION, 2021, "grand finals", "NM5", None, "OTHER", "")
    assert [c["label"] for c in await balance.corpus_table(factory, 1) if c["slot"] == "NM5"] == ["NM5"]
    await balance.label_corpus(factory, 1, corpus.CORPUS_VERSION, 2021, "grand finals", "NM5", 3, "NM5", "пятый NM пока не в дуэлях")
    cells = await balance.corpus_table(factory, 1)
    assert [m["label"] for c in cells if c["slot"] == "NM5" for m in c["maps"] if m["beatmap_id"] == 3] == ["NM5"]
    rows = await importer.corpus_rows(factory)
    assert sorted((row[1], row[5]) for row in rows) == [("FL1", False), ("HD2", False), ("NM3", True), ("NM5", True), ("TB", False)]
    added = await balance.add_tournament(factory, 1, "Russian Cup", 2024,
                                         "NM1 https://osu.ppy.sh/beatmapsets/80#osu/8\nDT1 https://osu.ppy.sh/b/9\nFM1 https://osu.ppy.sh/b/10\nтекст", "мой турнир")
    assert added == {"added": 3, "read": 3}
    assert await importer.load_missing(factory, fetch, str(tmp_path), pause=0, report=lambda text: None) == {"filed": 3}
    rows = await importer.corpus_rows(factory)
    own = sorted((row[1], row[4], row[5]) for row in rows if row[4])
    assert own == [("DT1", True, True), ("FM1", True, True), ("NM1", True, True)]
    version, found = await training.train(factory, steps=10)
    assert version == "kamerton-1" and found["own"] == 3 and found["verified"] == 5
    async with factory() as session:
        actions = sorted(row.action for row in (await session.scalars(select(DuelAudit))).all())
    assert actions == ["add_tournament", "corpus_label", "corpus_label", "corpus_label", "corpus_label", "corpus_label", "corpus_label"]


async def test_mirror_search_sends_star_filter_and_tries_fallback_after_empty():
    from urllib.parse import urlparse, parse_qs
    requested = []
    raw = _map(1)
    async def fetch(url):
        requested.append(url)
        if "osu.direct" in url:
            query = parse_qs(urlparse(url).query)["q"][0]
            assert query == "[beatmaps.difficulty_rating >= 7.5 AND beatmaps.difficulty_rating < 8]"
            return b"[]"
        return json.dumps({"beatmapsets": [_set(10, [(1, 7.7, "osu", 120, raw), (2, 8.0, "osu", 120, raw)])]}).encode()
    name, entries, count = await mirrors.search(fetch, "ranked", 0, 50, 7.5, 8.0)
    assert name == "catboy.best" and len(requested) == 2
    assert count == 1 and [e["beatmap_id"] for e in entries] == [1]


async def test_error_json_is_not_a_successful_empty_search():
    requested = []
    async def fetch(url):
        requested.append(url)
        return b'{"error":"maintenance"}'
    assert await mirrors.search(fetch, "ranked", 0, 50, 7.5, 8.0) is None
    assert len(requested) == 2


async def test_the_owner_sees_renames_excludes_and_deletes_reviewers_batches_whole_or_map_by_map(factory, monkeypatch):
    from db.models.duel import DuelAudit, DuelRole
    from db.models.duel_catalog import DuelTrainingExample
    from db.models.player import Player
    from services.duels import balance
    from services.duels.errors import DuelError

    async with factory() as session:
        session.add_all([Player(id=1, osu_user_id=101, osu_username="owner", telegram_id=555), Player(id=2, osu_user_id=102, osu_username="reviewer")])
        await session.flush()
        session.add(DuelRole(player_id=2, role=balance.BALANCE_REVIEWER))
        await session.commit()
    pool = "NM1 https://osu.ppy.sh/b/11\nNM2 https://osu.ppy.sh/b/12\nhttps://osu.ppy.sh/b/13"
    await balance.add_tournament(factory, 2, "Russian Cup", 2024, pool, "our pool")
    await balance.add_tournament(factory, 2, "Other Cup", 2023, "NM1 https://osu.ppy.sh/b/21", "another")
    with pytest.raises(balance.Forbidden):
        await balance.batches(factory, 2)
    found = await balance.batches(factory, 1, owners=(555,))
    russian = next(batch for batch in found if batch["name"] == "Russian Cup")
    assert (russian["year"], russian["maps"], russian["labelled"], russian["excluded"]) == (2024, 3, 2, 0)
    assert russian["author"] == {"player": 2, "name": "reviewer", "osu_id": 102} and russian["added_at"]
    with pytest.raises(DuelError) as clash:
        await balance.rename_batch(factory, 1, "Russian Cup", 2024, "Other Cup", 2023, "same as another", owners=(555,))
    assert clash.value.code == "BATCH_EXISTS"
    assert await balance.rename_batch(factory, 1, "Russian Cup", 2024, "RC Finals", 2025, "clearer name", owners=(555,)) == {"name": "RC Finals", "year": 2025}
    renamed = next(batch for batch in await balance.batches(factory, 1, owners=(555,)) if batch["name"] == "RC Finals")
    assert (renamed["year"], renamed["maps"], renamed["labelled"]) == (2025, 3, 2), "labels travel with the batch"
    await balance.exclude_batch(factory, 1, "RC Finals", 2025, 12, True, "mislabelled", owners=(555,))

    async def seen(session, digest):
        return {"objects": 1}, {}

    monkeypatch.setattr(importer, "seen_of", seen)
    async with factory() as session:
        for row in (await session.scalars(select(DuelTrainingExample))).all():
            row.revision_hash = f"{row.beatmap_id:032x}"
        await session.commit()
    learned = await importer.corpus_rows(factory)
    assert sorted(row[2] for row in learned) == [11, 21], "an excluded map and an unlabelled one stay out of training"
    await balance.exclude_batch(factory, 1, "RC Finals", 2025, None, False, "back in", owners=(555,))
    assert sorted(row[2] for row in await importer.corpus_rows(factory)) == [11, 12, 21]
    assert await balance.delete_batch(factory, 1, "RC Finals", 2025, 11, "not this map", owners=(555,)) == {"maps": 1}
    assert await balance.delete_batch(factory, 1, "Other Cup", 2023, None, "test batch", owners=(555,)) == {"maps": 1}
    with pytest.raises(DuelError) as gone:
        await balance.delete_batch(factory, 1, "Other Cup", 2023, None, "twice", owners=(555,))
    assert gone.value.code == "BATCH_NOT_FOUND"
    assert [(batch["name"], batch["maps"]) for batch in await balance.batches(factory, 1, owners=(555,))] == [("RC Finals", 2)]
    async with factory() as session:
        actions = [row.action for row in (await session.scalars(select(DuelAudit).order_by(DuelAudit.id))).all()]
    assert actions[2:] == ["batch_rename", "batch_exclude", "batch_include", "batch_delete", "batch_delete"]



async def test_an_old_training_table_gains_the_exclusion_flag_once(tmp_path, monkeypatch):
    from sqlalchemy import text

    from db.migrations._utils import existing_columns
    from db.migrations.add_duel_batches import run_duel_batches_migration

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text('ALTER TABLE "duel_training_examples" DROP COLUMN "excluded"'))
        await conn.execute(text("INSERT INTO duel_training_examples (corpus_version, source, year, stage, original_slot, beatmap_id, revision_hash, "
                                "conflict, fetched_at) VALUES ('own-v1', 'own:Cup', 2024, 'Cup', 'NM1', 7, 'f00d', FALSE, '2026-10-27 00:00:00')"))
    await run_duel_batches_migration(engine)
    await run_duel_batches_migration(engine)
    async with engine.begin() as conn:
        assert "excluded" in await existing_columns(conn, "duel_training_examples")
    made = async_sessionmaker(engine, expire_on_commit=False)

    async def seen(session, digest):
        return {"objects": 1}, {}

    monkeypatch.setattr(importer, "seen_of", seen)
    assert [row[2] for row in await importer.corpus_rows(made)] == [7], "an old row without the flag still trains"
    async with made() as session:
        from db.models.duel_catalog import DuelTrainingExample
        row = await session.scalar(select(DuelTrainingExample))
        assert row.excluded is None
    await engine.dispose()
