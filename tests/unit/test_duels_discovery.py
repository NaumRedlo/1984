from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from db.models.duel_catalog import DuelMapAssignment, DuelMapRevision, DuelTrainingExample
from services.duels import discovery, generator, mirrors
from services.duels.generator import Candidate, Skill
from services.duels.rules import SLOTS
from tests.unit.test_duels_import import Osu, _map, factory

class Model:
    version = "fixture-model"

    def predict(self, features, base_ar, hr_cs, views):
        assert "NFDT" in views and "NFHR" in views
        return SimpleNamespace(slot="DT1", probability=0.91, margin=0.4)


def candidates(each=1):
    return [Candidate(n, n, f"mapper{n}", slot, 5.5, "human", 1.0, f"{n:032x}")
            for n, slot in enumerate([slot for slot in SLOTS for _ in range(each)], start=1)]

@pytest.mark.parametrize("count", [4, 5])
def test_mixed_pools_keep_the_quota_even_when_catalogue_maps_score_better(count):
    known = candidates(3)
    new = [replace(c, beatmap_id=c.beatmap_id + 100, beatmapset_id=c.beatmapset_id + 100, mapper=f"new{c.beatmap_id}",
                   source="model", level="confident", confidence=0.81) for c in known]
    skills = {slot: Skill(5.5, 0.5) for slot in SLOTS}
    result = generator.generate(known + new, skills, skills, "mixed", generated_count=count)
    assert result.status == "ready"
    assert len(result.entries) == 9
    assert sum(c.candidate.source == "model" for c in result.entries) == count
    assert result == generator.generate(known + new, skills, skills, "mixed", generated_count=count)


def test_a_shortage_of_new_maps_does_not_silently_become_an_all_catalogue_pool():
    skills = {slot: Skill(5.5, 0.5) for slot in SLOTS}
    assert generator.generate(candidates(), skills, skills, "mixed", generated_count=4).status == "unavailable"


async def test_discovered_maps_use_modded_stars_without_catalogue_admission(factory, tmp_path, monkeypatch):
    async def fetch(*args):
        return "fixture", _map(91)
    monkeypatch.setattr(mirrors, "fetch_osu", fetch)
    entry = dict(beatmap_id=91, beatmapset_id=9, status="ranked", length=120, stars=5.0)
    candidate = await discovery.inspect(factory, None, Osu(), str(tmp_path), entry, Model())
    assert (candidate.beatmap_id, candidate.slot, candidate.stars, candidate.source) == (91, "DT1", 6.5, "model")
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(DuelMapAssignment)) == 0
        assert await session.scalar(select(func.count()).select_from(DuelMapRevision)) == 1
    assert await discovery.cached(factory, Model(), set()) == [candidate]
    async with factory() as session:
        session.add(DuelMapAssignment(beatmap_id=91, state="QUARANTINED", admitted=False))
        session.add(DuelTrainingExample(beatmap_id=92, corpus_version="fixture", source="batch", year=0,
                                       stage="batch", original_slot="DT1"))
        await session.commit()
        blocked = await discovery.excluded(session)
    assert {91, 92} <= blocked
    assert await discovery.cached(factory, Model(), blocked) == []
    assert await discovery.inspect(factory, None, Osu(), str(tmp_path), entry, Model()) is None


@pytest.mark.parametrize("slot,probability,margin", [(None, 0.99, 0.9), ("DT1", 0.7, 0.4), ("DT1", 0.9, 0.1), ("FM3", 0.99, 0.9)])
def test_unseen_maps_require_confident_in_distribution_predictions(slot, probability, margin):
    class Refusing(Model):
        def predict(self, *args):
            return SimpleNamespace(slot=slot, probability=probability, margin=margin)
    revision = SimpleNamespace(status="ok", ranked_status="ranked")
    analysis = SimpleNamespace(features={"effective": {"ar": 9, "cs": 5.2}}, stars=6.5)
    assert discovery.predicted(Refusing(), revision, {"NF": analysis, "NFHR": analysis, "NFDT": analysis}) is None
