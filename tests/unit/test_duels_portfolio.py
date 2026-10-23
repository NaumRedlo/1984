from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import db.models
from db.database import Base
from db.models.duel_catalog import DuelMapAnalysis, DuelMapAssignment, DuelMapRevision
from db.models.map_attempt import UserMapAttempt
from db.models.player import Player
from services.duels import portfolio
from services.duels.features import FEATURE_VERSION
from services.duels.portfolio import Observation

NOW = datetime(2026, 10, 23, 12, 0, 0)

def test_quality_and_reach_follow_the_formulas():
    assert portfolio.quality(1.0, 500, 500, 0, 400) == pytest.approx(1.0)
    assert portfolio.quality(0.9, 250, 500, 4, 400) == pytest.approx(0.65 * 0.9 + 0.25 * 0.5 + 0.10 * 0.99)
    assert portfolio.quality(0.9, 900, 500, 0, 400) == pytest.approx(0.65 * 0.9 + 0.25 + 0.10)
    assert portfolio.quality(0.9, 100, 0, 0, 400) is None and portfolio.quality(1.2, 1, 1, 0, 1) is None
    assert portfolio.reach(5.0, 0.85) == pytest.approx(5.0)
    assert portfolio.reach(5.0, 1.0) == pytest.approx(5.3)
    assert portfolio.reach(5.0, 0.0) == pytest.approx(3.3)
    assert portfolio.age_weight(1.0, NOW - timedelta(days=90), NOW) == pytest.approx(0.5)

def _seen(beatmap, value, days=0, slot="NM1"):
    return Observation(beatmap, slot, value, value, 1.0, NOW - timedelta(days=days))

def test_few_maps_keep_a_wide_uncertainty_and_many_narrow_it():
    few = portfolio.estimate([_seen(1, 5.0), _seen(2, 5.2)], NOW)
    assert few.u == pytest.approx(1.2 / 2 ** 0.5) and few.theta == 5.2
    single = portfolio.estimate([_seen(1, 5.0), _seen(1, 5.0, days=3)], NOW)
    assert single.u == pytest.approx(1.2)
    many = portfolio.estimate([_seen(n, 5.0 + n * 0.01) for n in range(30)], NOW)
    assert many.u == pytest.approx(max(0.25, 1.2 / 30 ** 0.5))
    assert 5.15 <= many.theta <= 5.2

def test_one_map_played_often_counts_once_while_few_maps_are_known():
    repeated = [_seen(1, 6.0, days=d) for d in range(5)] + [_seen(2, 4.0)]
    found = portfolio.estimate(repeated, NOW)
    assert found.theta == 6.0 and found.u == pytest.approx(1.2 / 2 ** 0.5)

def test_a_profile_borrows_for_slots_without_plays_and_starts_from_the_prior():
    skills, marks = portfolio.profile([_seen(n, 5.0) for n in range(5)], NOW)
    assert marks["NM1"] == "measured" and marks["DT2"] == "UNKNOWN_SLOT"
    assert skills["DT2"].theta == pytest.approx(5.0) and skills["DT2"].u >= 1.2
    empty, empty_marks = portfolio.profile([], NOW)
    assert empty["NM1"] == portfolio.PRIOR and empty_marks["NM1"] == "PRIOR"

def test_only_plays_with_the_slot_mods_teach_that_slot():
    assert portfolio.fits_slot("HD2", frozenset({"HD"})) and portfolio.fits_slot("HD2", frozenset({"HD", "NF"}))
    assert not portfolio.fits_slot("HD2", frozenset({"HD", "HR"})) and not portfolio.fits_slot("NM1", frozenset({"HD"}))
    assert portfolio.fits_slot("DT1", frozenset({"DT", "CL"})) and portfolio.fits_slot("NM3", frozenset())

@pytest_asyncio.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'portfolio.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    made = async_sessionmaker(engine, expire_on_commit=False)
    async with made() as session:
        session.add(Player(id=1, osu_user_id=101, osu_username="one"))
        await session.flush()
        digest = "a" * 32
        session.add(DuelMapRevision(beatmap_id=7, beatmapset_id=70, hash=digest, status="ok", ranked_status="ranked", max_combo=600,
                                    object_count=450))
        session.add(DuelMapAssignment(beatmap_id=7, slot="HD2", state="HUMAN_APPROVED", revision_hash=digest, admitted=True))
        session.add(DuelMapAnalysis(revision_hash=digest, mods="NFHD", engine_version="t", feature_version=FEATURE_VERSION,
                                    model_version="m", stars=5.5, features={}))
        for number, mods in enumerate(("HD", "HD,HR", None)):
            session.add(UserMapAttempt(player_id=1, score_id=number + 1, beatmap_id=7, pp=200, accuracy=98.0, max_combo=600,
                                       mods=mods, map_max_combo=600, total_objects=450, count_miss=0, passed=True, ruleset=0,
                                       played_at=NOW - timedelta(days=number)))
        await session.commit()
        yield session
    await engine.dispose()

async def test_submitted_scores_with_the_slot_mods_become_observations(session):
    assert await portfolio.from_attempts(session, 1) == 1
    assert await portfolio.from_attempts(session, 1) == 1
    seen = await portfolio.observations_of(session, 1)
    assert len(seen) == 1 and seen[0].slot == "HD2"
    assert seen[0].value == pytest.approx(portfolio.reach(5.5, 0.65 * 0.98 + 0.25 + 0.10))
