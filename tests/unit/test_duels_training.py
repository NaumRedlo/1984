import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from db.database import Base
from db.models.duel import DuelModelVersion, DuelPoolEntry
from db.models.duel_catalog import DuelMapAnalysis, DuelMapAssignment, DuelMapRevision
from db.models.player import Player
from services.duels import balance, training
from services.duels.errors import DuelError
from services.duels.features import FEATURE_VERSION
from tests.unit.test_duels_balance import _labelled
from tests.unit.test_duels_series import Clock, _drafting, _view


@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'training.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    made = async_sessionmaker(engine, expire_on_commit=False)
    async with made() as session:
        session.add_all([Player(id=1, osu_user_id=101, osu_username="one"), Player(id=2, osu_user_id=102, osu_username="two")])
        await session.commit()
    yield made
    await engine.dispose()


async def test_an_excluded_map_leaves_training_its_report_and_the_change_check_together(factory):
    from db.models.duel_catalog import DuelTrainingExample
    from services.duels import kamerton
    from tests.unit.test_duels_analysis import _features

    await _labelled(factory, 20)
    async with factory() as session:
        session.add(DuelTrainingExample(corpus_version="own-v1", source="own:Cup", year=2024, stage="Cup", original_slot="NM2", beatmap_id=77,
                                        beatmapset_id=770, revision_hash="e" * 32, normalized_label="NM2", conflict=False))
        session.add(DuelMapAnalysis(revision_hash="e" * 32, mods="NF", engine_version="t", feature_version=FEATURE_VERSION, model_version="m",
                                    stars=5.0, features=_features("stream", 77)))
        await session.commit()
    _, first = await training.train(factory, steps=20)
    assert (first["maps"], first["own"]) == (21, 1)
    async with factory() as session:
        (await session.scalar(select(DuelTrainingExample))).excluded = True
        await session.commit()
    assert 770 not in [example.set_id for example in await kamerton.examples(factory)]
    assert not await training.unchanged(factory), "excluding a map is a change worth training on"
    _, second = await training.train(factory, steps=20)
    assert (second["maps"], second["own"]) == (20, 0), "the report counts exactly what was trained on"


async def test_a_new_secondary_slot_is_new_data_for_training(factory):
    await _labelled(factory, 20)
    assert (await training.train(factory, steps=20))[0] == "kamerton-1" and await training.unchanged(factory)
    async with factory() as session:
        for row in (await session.scalars(select(DuelMapAssignment).where(DuelMapAssignment.slot == "NM1"))).all():
            row.secondary_slots = ["HR1"]
        await session.commit()
    assert not await training.unchanged(factory)
    version, report = await training.train(factory, steps=20)
    assert version == "kamerton-2" and report["secondary"] == 10
    async with factory() as session:
        body = (await session.get(DuelModelVersion, version)).body
    assert "HR1" in body["slots"]["classes"], "the slot layer learns the secondary slot as an answer of its own"


async def test_a_model_that_loads_but_cannot_predict_is_not_published(factory):
    await _labelled(factory, 20)
    version, _ = await training.train(factory, steps=20)
    async with factory() as session:
        (await session.get(Player, 1)).telegram_id = 555
        held = await session.get(DuelModelVersion, version)
        held.body = {**held.body, "skills": {**held.body["skills"], "weights": []}}
        await session.commit()
    with pytest.raises(DuelError) as refused:
        await balance.publish(factory, 1, version, "empty skill weights", owners=(555,))
    assert refused.value.code == "INVALID_STATE" and await balance.published(factory) is None


async def test_a_training_job_reports_done_nothing_new_or_failed(factory, monkeypatch):
    import asyncio

    name = training.open_job()
    assert training.jobs[name]["state"] == "running" and training.running_job() == name
    assert await training.run_job(factory, name, steps=20) is None
    assert training.jobs[name]["state"] == "nothing_new" and training.running_job() is None
    await _labelled(factory, 20)
    name = training.open_job()
    version, _ = await training.run_job(factory, name, steps=20)
    assert (training.jobs[name]["state"], training.jobs[name]["version"]) == ("done", version)

    async def stumble(factory, steps=400, now=None, report=None):
        await asyncio.sleep(0)
        raise RuntimeError("disk full")

    monkeypatch.setattr(training, "train", stumble)
    name = training.open_job()
    with pytest.raises(RuntimeError):
        await training.run_job(factory, name)
    assert training.jobs[name]["state"] == "failed" and "disk full" in training.jobs[name]["error"]
    for _ in range(training.JOBS_KEPT + 5):
        training.jobs[training.open_job()]["state"] = "done"
    assert len(training.jobs) <= training.JOBS_KEPT


async def test_a_model_that_refuses_a_map_takes_back_its_automatic_slot_but_not_a_person_s_or_a_drawn_pool(factory):
    from services.duels import catalog, importer
    from services.duels.slot_model import Prediction
    from tests.unit.test_duels_series import _pool

    clock = Clock()
    seen = {"effective": {"ar": 9.0, "cs": 4.0}, "objects": 100}
    drawn = {entry.candidate.beatmap_id: entry.candidate.slot for entry in _pool().entries}
    maps = {**drawn, 900: "NM2", 901: "DT1"}
    async with factory() as session:
        for beatmap, slot in maps.items():
            digest = f"{beatmap:032x}"
            session.add(DuelMapRevision(beatmap_id=beatmap, beatmapset_id=beatmap, hash=digest, status="ok", ranked_status="ranked"))
            session.add(DuelMapAssignment(beatmap_id=beatmap, slot=slot, state="HUMAN_APPROVED" if beatmap == 900 else "AUTO_ASSIGNED",
                                          confidence=0.65 if beatmap == 901 else 0.9, margin=0.3, revision_hash=digest, admitted=True))
            for mods in ("NF", "NFHR"):
                session.add(DuelMapAnalysis(revision_hash=digest, mods=mods, engine_version="t", feature_version=FEATURE_VERSION, model_version="m",
                                            stars=5.0, features=seen))
        await session.commit()
    duel_id = await _drafting(factory, clock)
    async with factory() as session:
        before = sorted((entry.beatmap_id, entry.slot, entry.review_state) for entry in (await session.scalars(select(DuelPoolEntry))).all())

    class Refusing:
        version = "kamerton-bad"

        def predict(self, features, base_ar, hr_cs, views=None):
            return Prediction(None, 0.95, 0.9, {"NM1": 0.95, "NM2": 0.05}, True)

    counted = await importer.reassess(factory, Refusing())
    assert counted == {"revoked": len(drawn) + 1}, "confident and unsure automatic slots alike are taken back"
    async with factory() as session:
        rows = {row.beatmap_id: row for row in (await session.scalars(select(DuelMapAssignment))).all()}
        after = sorted((entry.beatmap_id, entry.slot, entry.review_state) for entry in (await session.scalars(select(DuelPoolEntry))).all())
        revisions = {row.beatmap_id: row for row in (await session.scalars(select(DuelMapRevision))).all()}
    assert all(rows[beatmap].slot is None and rows[beatmap].state == "UNASSIGNED" for beatmap in maps if beatmap != 900)
    assert all(catalog.playable(rows[beatmap], revisions[beatmap]) is None for beatmap in maps if beatmap != 900)
    assert (rows[900].slot, rows[900].state) == ("NM2", "HUMAN_APPROVED"), "a person's decision stays"
    assert after == before and (await _view(factory, duel_id, 1))["pool"]["entries"], "the pool already shown keeps its maps"

    class Sure:
        version = "kamerton-good"

        def predict(self, features, base_ar, hr_cs, views=None):
            return Prediction("DT2", 0.9, 0.8, {"DT2": 0.9, "DT1": 0.1}, False)

    async with factory() as session:
        rows = (await session.scalars(select(DuelMapAssignment).where(DuelMapAssignment.beatmap_id == 901))).one()
        rows.slot, rows.state = "DT1", "AUTO_ASSIGNED"
        await session.commit()
    counted = await importer.reassess(factory, Sure())
    assert counted == {"proposed DT2": len(drawn), "moved DT2": 1}
    async with factory() as session:
        actions = [row.action for row in (await session.scalars(select(catalog.DuelAssignmentLog).where(catalog.DuelAssignmentLog.beatmap_id == 901)
                                                                .order_by(catalog.DuelAssignmentLog.id))).all()]
    assert actions[-2:] == ["revoke", "move"]
