from datetime import timedelta
import pytest

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import db.models
from db.database import Base
from db.models.duel import Duel, DuelAudit, DuelBalanceReview, DuelFeedback, DuelParticipant, DuelRole
from db.models.duel_catalog import DuelMapAnalysis, DuelMapAssignment, DuelMapRevision
from db.models.player import Player
from services.duels import desk, states
from services.duels.features import FEATURE_VERSION
from utils.timeutils import utcnow

@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'desk.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    made = async_sessionmaker(engine, expire_on_commit=False)
    async with made() as session:
        session.add_all([Player(id=1, osu_user_id=101, osu_username="one"), Player(id=2, osu_user_id=102, osu_username="two")])
        await session.flush()
        session.add_all([DuelRole(player_id=1, role="BALANCE_REVIEWER"), DuelRole(player_id=1, role="TECHNICAL_OPERATOR")])
        rows = [(1, "NM1", "HUMAN_APPROVED", 5.2, None), (2, "NM1", "AUTO_ASSIGNED", 6.1, (0.9, 0.5)), (3, "NM2", "AUTO_ASSIGNED", 6.6, (0.65, 0.15)),
                (4, None, "UNASSIGNED", 5.0, None), (5, "DT1", "QUARANTINED", 6.0, None)]
        for number, slot, state, stars, sure in rows:
            digest = f"{number:032x}"
            session.add(DuelMapRevision(beatmap_id=number, beatmapset_id=number * 10, hash=digest, status="ok", ranked_status="ranked", mapper="Meg"))
            session.add(DuelMapAssignment(beatmap_id=number, slot=slot, state=state, revision_hash=digest, admitted=True,
                                          confidence=sure[0] if sure else None, margin=sure[1] if sure else None))
            features = {"effective": {"ar": 9.0, "cs": 4.0}}
            for mods in ("NF", "NFHR", "NFDT"):
                session.add(DuelMapAnalysis(revision_hash=digest, mods=mods, engine_version="t", feature_version=FEATURE_VERSION,
                                            model_version="none", stars=stars, features=features))
        session.add(DuelBalanceReview(beatmap_id=3, revision_hash=f"{3:032x}", reason="uncertain_revealed", requested_at=utcnow()))
        old = utcnow() - timedelta(minutes=9)
        session.add(Duel(id="stuck-1", mode="rated", state=states.PAUSED, rules_version="v1", seed="s", pause_reason="absence", updated_at=old,
                         created_at=old, wins_a=1, wins_b=0))
        session.add(Duel(id="done-1", mode="rated", state=states.COMPLETED, rules_version="v1", seed="s", finalized_at=utcnow(), winner_id=2,
                         wins_a=1, wins_b=3))
        await session.flush()
        session.add_all([DuelParticipant(duel_id="stuck-1", player_id=1, osu_user_id=101, side="A"),
                         DuelParticipant(duel_id="stuck-1", player_id=2, osu_user_id=102, side="B"),
                         DuelParticipant(duel_id="done-1", player_id=1, osu_user_id=101, side="A"),
                         DuelParticipant(duel_id="done-1", player_id=2, osu_user_id=102, side="B")])
        session.add(DuelFeedback(duel_id="done-1", player_id=2, slot="NM2", beatmap_id=3, played=True, fit_label="off", difficulty_label="hard",
                                 comment="это не стримы", history=[]))
        session.add(DuelAudit(actor_id=1, role="BALANCE_REVIEWER", action="map_approve", target="1", reason="так и есть"))
        await session.commit()
    yield made
    await engine.dispose()

async def test_the_catalogue_is_counted_by_slot_and_star_band(factory):
    found = await desk.coverage(factory, 1)
    assert found["grid"]["NM1"][desk.band_of(5.2)] == 1 and found["grid"]["NM1"][desk.band_of(6.1)] == 1
    assert found["grid"]["NM2"][desk.band_of(6.6)] == 1
    assert found["totals"] == {"playable": 3, "human": 1, "uncertain": 1, "quarantined": 1, "unassigned": 1}
    cell = await desk.coverage_cell(factory, 1, "NM1", desk.band_of(6.1))
    assert [(m["beatmap_id"], m["level"]) for m in cell] == [(2, "confident")]
    assert desk.band_of(3.2) == 0 and desk.band_of(9.0) == len(desk.BANDS) - 1

async def test_the_queue_lists_reviews_first_then_maps_waiting_for_a_slot(factory):
    class Model:
        version = "slots-test"

        def predict(self, features, base_ar, hr_cs, views=None):
            from services.duels.slot_model import Prediction
            return Prediction("NM2", 0.7, 0.3, {"NM2": 0.7, "NM3": 0.2, "OTHER": 0.1}, False, [("streams.share", 1.2)])

    tasks = await desk.queue(factory, 1, model=Model())
    assert [(t["kind"], t["map"]["beatmap_id"]) for t in tasks] == [("review", 3), ("unassigned", 4)]
    first = tasks[0]
    assert first["slot"] == "NM2" and first["opinions"]["off"] == 1 and first["opinions"]["difficulty"] == {"hard": 1}
    assert first["opinions"]["comments"] == ["это не стримы"]
    assert first["map"]["model"]["top"][0] == {"slot": "NM2", "probability": 0.7} and first["map"]["model"]["reasons"][0]["feature"] == "streams.share"

def test_a_map_shows_its_own_difficulty_settings(tmp_path):
    (tmp_path / "abc.osu").write_text("osu file format v14\n\n[Difficulty]\nHPDrainRate:6\nCircleSize:4.2\nOverallDifficulty:9\n"
                                      "ApproachRate:9.6\nSliderMultiplier:1.8\n\n[Events]\nHPDrainRate:1\n", encoding="utf-8")
    assert desk.settings_of("abc", str(tmp_path)) == {"hp": 6.0, "cs": 4.2, "od": 9.0, "ar": 9.6}
    (tmp_path / "old.osu").write_text("osu file format v5\n[Difficulty]\nHPDrainRate:5\nCircleSize:4\nOverallDifficulty:7\n", encoding="utf-8")
    assert desk.settings_of("old", str(tmp_path))["ar"] == 7.0
    assert desk.settings_of("missing", str(tmp_path)) == {} and desk.settings_of(None, str(tmp_path)) == {}

async def test_maps_kamerton_is_unsure_about_wait_in_the_queue_before_maps_without_a_slot(factory):
    async with factory() as session:
        digest = f"{6:032x}"
        session.add(DuelMapRevision(beatmap_id=6, beatmapset_id=60, hash=digest, status="ok", ranked_status="ranked", mapper="Meg", version="Insane"))
        session.add(DuelMapAssignment(beatmap_id=6, slot="DT2", state="AUTO_ASSIGNED", revision_hash=digest, admitted=True, confidence=0.62, margin=0.11))
        for mods, stars, ar, length in (("NF", 5.0, 9.0, 150.0), ("NFDT", 7.0, 10.33, 100.0)):
            session.add(DuelMapAnalysis(revision_hash=digest, mods=mods, engine_version="t", feature_version=FEATURE_VERSION, model_version="none",
                                        stars=stars, features={"effective": {"ar": ar, "cs": 4.0, "od": 8.0}, "length_seconds": length}))
        await session.commit()
    tasks = await desk.queue(factory, 1)
    assert [(t["kind"], t["reason"], t["map"]["beatmap_id"]) for t in tasks] == [
        ("review", "uncertain_revealed", 3), ("review", "uncertain", 6), ("unassigned", "unassigned", 4)]
    assert tasks[1]["slot"] == "DT2"
    played = tasks[1]["map"]
    assert (played["stars"], played["plain_stars"], played["mods"], played["length"]) == (7.0, 5.0, ["DT"], 100.0)
    assert played["settings"].get("ar") == 10.33
    cell = await desk.coverage_cell(factory, 1, "DT2", desk.band_of(5.0))
    assert cell == [] or all("version" in found for found in cell)

async def test_stuck_duels_the_journal_the_people_and_the_live_list(factory):
    stuck = await desk.stuck(factory, 1)
    assert [(d["id"], d["reason"], d["wins"]) for d in stuck] == [("stuck-1", "absence", {"A": 1, "B": 0})]
    assert [p["name"] for p in stuck[0]["players"]] == ["one", "two"]
    entries = await desk.journal(factory, 1)
    assert [(e["name"], e["action"]) for e in entries] == [("one", "map_approve")]
    assert await desk.people(factory, 1) == [{"player": 1, "name": "one", "roles": ["BALANCE_REVIEWER", "TECHNICAL_OPERATOR"]}]
    shown = await desk.live(factory, watchers=lambda duel_id: 4)
    assert [d["id"] for d in shown["running"]] == ["stuck-1"] and shown["running"][0]["watching"] == 4
    assert [(d["id"], d["winner"]) for d in shown["finished"]] == [("done-1", 2)] and shown["finished"][0]["seconds_ago"] < 60

async def test_a_search_on_the_mirrors_is_kept_in_the_journal_and_needs_a_reviewer(factory):
    from services.duels import balance

    with pytest.raises(balance.Forbidden):
        await balance.note_discovery(factory, 2, 5.5, 6.0, "мало альтернейта")
    await balance.note_discovery(factory, 1, 5.5, 6.0, "мало альтернейта")
    entries = await desk.journal(factory, 1)
    assert (entries[0]["action"], entries[0]["target"], entries[0]["reason"]) == ("discover", "5.5-6.0", "мало альтернейта")


def test_discovery_uses_half_star_windows_even_for_the_open_top_column():
    assert desk.discovery_range(0) == (4.0, 4.5)
    assert desk.discovery_range(7) == (7.5, 8.0)
    for bad in (-1, 8, True):
        with pytest.raises(ValueError):
            desk.discovery_range(bad)


async def test_confident_maps_can_be_inspected_moved_and_removed_by_reviewers(factory):
    from services.duels import balance
    task = await desk.inspect_map(factory, 1, 2)
    assert task["map"]["beatmap_id"] == 2 and task["state"] == "AUTO_ASSIGNED"
    with pytest.raises(balance.Forbidden):
        await desk.inspect_map(factory, 2, 2)
    changed = await balance.review_map(factory, 1, 2, "MOVE", "corrected", slot="FM4")
    assert changed["slot"] == "FM4" and changed["state"] == "HUMAN_APPROVED"
    removed = await balance.review_map(factory, 1, 2, "REJECT", "not suitable")
    assert removed["state"] == "REJECTED"
    assert await desk.inspect_map(factory, 1, 9999) is None


async def test_presence_requires_a_role_updates_context_and_expires(factory):
    from services.duels import balance, presence
    now = utcnow()
    with pytest.raises(balance.Forbidden):
        await presence.touch(factory, 2, "queue", 2, now=now)
    async with factory() as session:
        session.add(DuelRole(player_id=2, role="BALANCE_REVIEWER"))
        await session.commit()
    await presence.touch(factory, 1, "queue", 2, now=now)
    live = await presence.touch(factory, 2, "label", 2, now=now)
    assert len(live) == 2
    assert [p["name"] for p in live if not p["mine"] and p["beatmap"] == 2] == ["one"]
    live = await presence.touch(factory, 1, "catalog", now=now + timedelta(seconds=10))
    assert next(p for p in live if p["mine"])["beatmap"] is None
    live = await presence.touch(factory, 1, "live", now=now + timedelta(seconds=46))
    assert len(live) == 1 and live[0]["mine"]
    assert await presence.touch(factory, 1, None, now=now + timedelta(seconds=47)) == []
    for section, beatmap in [([], None), ("not-a-section", None), ("queue", True), ("queue", -1)]:
        with pytest.raises(ValueError):
            await presence.touch(factory, 1, section, beatmap, now=now)


def test_bpm_uses_the_longest_timing_section_and_ignores_inherited_points(tmp_path):
    (tmp_path / "map.osu").write_text("[TimingPoints]\n0,500,4,2,0,100,1,0\n2000,-100,4,2,0,100,0,0\n3000,400,4,2,0,100,1,0\n[HitObjects]\n1,1,10000,1,0\n")
    assert desk.bpm_of("map", str(tmp_path)) == 150.0
    assert desk.bpm_of("missing", str(tmp_path)) is None


async def test_unlabelled_batch_waits_for_a_person_and_accepts_bare_ids(factory):
    from sqlalchemy import select
    from services.duels import balance, corpus, importer
    from db.models.duel_catalog import DuelTrainingExample
    result = await balance.add_tournament(factory, 1, "Speed samples", 0,
        "123\nhttps://osu.ppy.sh/beatmaps/456\nDT1 789\n123\nhttps://osu.ppy.sh/beatmapsets/999", "batch")
    assert result == {"added": 3, "read": 3}
    async with factory() as session:
        rows = (await session.scalars(select(DuelTrainingExample).order_by(DuelTrainingExample.beatmap_id))).all()
        labels = await importer.corpus_labels(session)
        assert [int(row.beatmap_id) for row in rows] == [123, 456, 789]
        assert all(len(row.original_slot) <= DuelTrainingExample.__table__.c.original_slot.type.length for row in rows)
        assert importer.resolved(rows[0], labels) == (None, False)
        assert importer.resolved(rows[1], labels) == (None, False)
        assert importer.resolved(rows[2], labels) == ("DT1", True)
    await balance.label_corpus(factory, 1, corpus.OWN_VERSION, 0, "Speed samples", "PENDING", 123, "FM4", "reviewed")
    async with factory() as session:
        labels = await importer.corpus_labels(session)
        assert importer.resolved(rows[0], labels) == ("FM4", True)

async def test_pool_probe_reports_catalogue_deficits_without_creating_a_duel(factory, monkeypatch):
    from sqlalchemy import func, select
    from scripts import duel_probe
    monkeypatch.setattr(duel_probe, "AsyncSessionFactory", factory)
    async with factory() as session:
        before = await session.scalar(select(func.count()).select_from(Duel))
    result = await duel_probe.probe(5.0, 5.2, True, "test")
    assert result["status"] == "unavailable" and result["deficits"]
    assert result["catalogue"] == {"NM1": 1}
    async with factory() as session:
        assert await session.scalar(select(func.count()).select_from(Duel)) == before
