from datetime import timedelta

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

        def predict(self, features, base_ar, hr_cs):
            from services.duels.slot_model import Prediction
            return Prediction("NM2", 0.7, 0.3, {"NM2": 0.7, "NM3": 0.2, "OTHER": 0.1}, False, [("streams.share", 1.2)])

    tasks = await desk.queue(factory, 1, model=Model())
    assert [(t["kind"], t["map"]["beatmap_id"]) for t in tasks] == [("review", 3), ("unassigned", 4)]
    first = tasks[0]
    assert first["slot"] == "NM2" and first["opinions"]["off"] == 1 and first["opinions"]["difficulty"] == {"hard": 1}
    assert first["opinions"]["comments"] == ["это не стримы"]
    assert first["map"]["model"]["top"][0] == {"slot": "NM2", "probability": 0.7} and first["map"]["model"]["reasons"][0]["feature"] == "streams.share"

async def test_stuck_duels_the_journal_the_people_and_the_live_list(factory):
    stuck = await desk.stuck(factory, 1)
    assert [(d["id"], d["reason"], d["wins"]) for d in stuck] == [("stuck-1", "absence", {"A": 1, "B": 0})]
    assert [p["name"] for p in stuck[0]["players"]] == ["one", "two"]
    entries = await desk.journal(factory, 1)
    assert [(e["name"], e["action"]) for e in entries] == [("one", "map_approve")]
    assert await desk.people(factory, 1) == [{"player": 1, "name": "one", "roles": ["BALANCE_REVIEWER", "TECHNICAL_OPERATOR"]}]
    shown = await desk.live(factory, watchers=lambda duel_id: 4)
    assert [d["id"] for d in shown["running"]] == ["stuck-1"] and shown["running"][0]["watching"] == 4
    assert [(d["id"], d["winner"]) for d in shown["finished"]] == [("done-1", 2)]
