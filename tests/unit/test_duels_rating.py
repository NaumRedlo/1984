from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import db.models
from db.database import Base
from db.models.duel import Duel, DuelGlicko, DuelParticipant, DuelRatingChange, DuelRatingResult
from db.models.player import Player
from services.duels import glicko, rating

def test_glickmans_own_example_comes_out_right():
    player = glicko.Rating(1500, 200, 0.06)
    games = [glicko.Game(glicko.Rating(1400, 30), 1), glicko.Game(glicko.Rating(1550, 100), 0), glicko.Game(glicko.Rating(1700, 300), 0)]
    after = glicko.update(player, games)
    assert after.rating == pytest.approx(1464.06, abs=0.02)
    assert after.rd == pytest.approx(151.52, abs=0.02)
    assert after.sigma == pytest.approx(0.059996, abs=0.000001)

def test_the_order_of_games_does_not_matter_and_rd_never_passes_its_ceiling():
    player = glicko.Rating(1500, 200, 0.06)
    games = [glicko.Game(glicko.Rating(1400, 30), 1), glicko.Game(glicko.Rating(1550, 100), 0), glicko.Game(glicko.Rating(1700, 300), 0)]
    assert glicko.update(player, games) == glicko.update(player, list(reversed(games)))
    rested = glicko.idle(glicko.Rating(1500, 340, 0.06), 1000)
    assert rested.rd == 350 and rested.rating == 1500
    assert glicko.idle(glicko.Rating(1500, 50, 0.06), 4).rd == pytest.approx(glicko.SCALE * ((50 / glicko.SCALE) ** 2 + 4 * 0.06 ** 2) ** 0.5)

def test_big_gaps_and_equal_players_stay_numbers():
    equal = glicko.update(glicko.Rating(), [glicko.Game(glicko.Rating(), 1)])
    assert equal.rating > 1500 and equal.rd < 350
    upset = glicko.update(glicko.Rating(800, 50), [glicko.Game(glicko.Rating(2900, 50), 1)])
    assert upset.rating > 800
    assert glicko.closeness(1500, 1500) == pytest.approx(0.5)

NOW = datetime(2026, 10, 23, 12, 30, 0)

@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rating.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    made = async_sessionmaker(engine, expire_on_commit=False)
    async with made() as session:
        session.add_all([Player(id=n, osu_user_id=100 + n, osu_username=f"p{n}") for n in (1, 2, 3)])
        await session.commit()
    yield made
    await engine.dispose()

async def _series(factory, number, first, second, winner, reason="played", mode="rated", at=NOW):
    async with factory() as session:
        duel = Duel(id=f"d{number}", mode=mode, state="COMPLETED", rules_version="duels-v1", seed="s", winner_id=winner)
        session.add(duel)
        await session.flush()
        people = [DuelParticipant(duel_id=duel.id, player_id=first, osu_user_id=100 + first, side="A"),
                  DuelParticipant(duel_id=duel.id, player_id=second, osu_user_id=100 + second, side="B")]
        session.add_all(people)
        made = await rating.record(session, duel, people, reason, at)
        await session.commit()
        return made

async def test_only_rated_finished_series_count(factory):
    assert await _series(factory, 1, 1, 2, 1) == 2
    assert await _series(factory, 2, 1, 2, 1, mode="direct") == 0
    assert await _series(factory, 3, 1, 2, None) == 0

async def test_a_period_is_applied_once_after_it_ends_with_everyone_from_its_start(factory):
    await _series(factory, 1, 1, 2, 1)
    await _series(factory, 2, 1, 3, 3)
    period = rating.period_of(NOW)
    assert await rating.process(factory, period, NOW) == "open"
    later = rating.period_end(period) + timedelta(seconds=1)
    assert await rating.sweep(factory, later) == 1
    assert await rating.sweep(factory, later) == 0
    assert await rating.process(factory, period, later) == "done"
    async with factory() as session:
        rows = {row.player_id: row for row in (await session.scalars(select(DuelGlicko))).all()}
        assert await session.scalar(select(func.count()).select_from(DuelRatingChange)) == 3
        assert await session.scalar(select(func.count()).select_from(DuelRatingResult).where(DuelRatingResult.processed_at.is_(None))) == 0
    expected_one = glicko.update(glicko.Rating(), [glicko.Game(glicko.Rating(), 1), glicko.Game(glicko.Rating(), 0)])
    assert rows[1].rating == pytest.approx(expected_one.rating) and rows[1].calibration_count == 2
    assert rows[2].rating < 1500 < rows[3].rating

async def test_technical_results_move_the_rating_but_not_calibration(factory):
    await _series(factory, 1, 1, 2, 1, reason="disconnect")
    later = rating.period_end(rating.period_of(NOW)) + timedelta(seconds=1)
    await rating.sweep(factory, later)
    shown = await rating.standing(factory, 1, later)
    assert shown["calibrating"] and shown["played"] == 0 and shown["rating"] is None and shown["provisional"] > 1500
    assert (await rating.standing(factory, 3, later))["played"] == 0

async def test_five_natural_series_finish_calibration_and_quiet_hours_widen_rd(factory):
    for number in range(5):
        await _series(factory, number, 1, 2, 1 if number % 2 else 2, at=NOW + timedelta(hours=number))
    end = NOW + timedelta(hours=6)
    assert await rating.sweep(factory, end) == 5
    shown = await rating.standing(factory, 1, end)
    assert not shown["calibrating"] and shown["rating"] is not None
    much_later = await rating.standing(factory, 1, end + timedelta(days=60))
    assert much_later["rd"] > shown["rd"]
