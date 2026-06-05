"""Unit tests for the single-track TrueSkill duel rating (services.duel.rating).

Covers the pure seed/scale helpers and the win/loss update against an
in-memory DB (StaticPool so the function's own session sees seeded rows).
"""

from __future__ import annotations

import contextlib

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from db.database import Base
from db.models.user import User  # noqa: F401 — registers table
from db.models.duel_rating import DuelRating
import services.duel.rating as rating
from services.duel.round_engine import _decide_round


# ── hardcore round scoring ───────────────────────────────────────────────────

def _stats(passed: bool, score: int):
    return {"passed": passed, "score": score, "accuracy": 99.0, "combo": 100, "misses": 0}


def test_decide_round_both_pass_higher_score_wins():
    assert _decide_round(_stats(True, 900_000), _stats(True, 800_000)) == 1
    assert _decide_round(_stats(True, 700_000), _stats(True, 950_000)) == 2


def test_decide_round_fail_scores_nothing():
    # Passing player wins even with a lower score than the failer.
    assert _decide_round(_stats(False, 999_999), _stats(True, 10)) == 2
    assert _decide_round(_stats(True, 10), _stats(False, 999_999)) == 1


def test_decide_round_both_fail_is_void():
    assert _decide_round(_stats(False, 500_000), _stats(False, 400_000)) is None


def test_decide_round_tie_score_goes_to_player1():
    # Exact score tie among passers resolves to player 1 (>= rule).
    assert _decide_round(_stats(True, 500_000), _stats(True, 500_000)) == 1


# ── pure helpers ─────────────────────────────────────────────────────────────

def test_starting_mu_monotonic_and_clamped():
    assert rating.starting_mu_from_pp(0) == rating._PP_MU_CURVE[0][1]
    assert rating.starting_mu_from_pp(10**9) == rating._PP_MU_CURVE[-1][1]
    seq = [rating.starting_mu_from_pp(pp) for pp in (0, 1000, 3000, 6000, 12000, 30000)]
    assert seq == sorted(seq), "mu seed must be non-decreasing in pp"


def test_rating_to_sr_clamped_and_monotonic():
    assert rating.rating_to_sr(0) == 1.5          # floor
    assert rating.rating_to_sr(10_000) == 10.0    # ceil
    assert rating.rating_to_sr(1000) < rating.rating_to_sr(2500)
    assert 1.5 <= rating.rating_to_sr(1500) <= 10.0


# ── DB-backed update ─────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def factory(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    @contextlib.asynccontextmanager
    async def _get_db_session():
        async with sm() as s:
            yield s

    monkeypatch.setattr(rating, "get_db_session", _get_db_session)
    yield sm
    await engine.dispose()


async def _seed(sm, mode="ranked"):
    async with sm() as s:
        u1 = User(chat_id=-100, telegram_id=1, osu_username="a")
        u2 = User(chat_id=-100, telegram_id=2, osu_username="b")
        s.add_all([u1, u2])
        await s.flush()
        s.add_all([
            DuelRating(user_id=u1.id, mode=mode, mu=1500.0, sigma=500.0, peak_mu=1500.0),
            DuelRating(user_id=u2.id, mode=mode, mu=1500.0, sigma=500.0, peak_mu=1500.0),
        ])
        await s.commit()
        return u1.id, u2.id


@pytest.mark.asyncio
async def test_winner_gains_loser_loses_sigma_shrinks(factory):
    w_id, l_id = await _seed(factory)
    w, l, w_old, w_new, l_old, l_new = await rating.update_ratings(w_id, l_id, "ranked")

    assert w.mu > 1500.0 > l.mu, "winner mu rises, loser mu falls"
    assert w.sigma < 500.0 and l.sigma < 500.0, "uncertainty shrinks for both"
    assert w.conservative > l.conservative
    assert (w.wins, w.losses, w.games) == (1, 0, 1)
    assert (l.wins, l.losses, l.games) == (0, 1, 1)
    assert w.placement_matches_left == rating.PLACEMENT_MATCHES - 1
    assert all(isinstance(d, str) and d for d in (w_old, w_new, l_old, l_new))
    assert w.peak_mu >= w.mu


@pytest.mark.asyncio
async def test_update_persists(factory):
    w_id, l_id = await _seed(factory)
    await rating.update_ratings(w_id, l_id, "ranked")
    async with factory() as s:
        w = (await s.execute(
            select(DuelRating).where(DuelRating.user_id == w_id))).scalar_one()
        assert w.wins == 1 and w.games == 1 and w.mu > 1500.0


@pytest.mark.asyncio
async def test_get_or_create_seeds_from_pp(factory):
    async with factory() as s:
        u = User(chat_id=-100, telegram_id=9, osu_username="c")
        s.add(u)
        await s.commit()
        uid = u.id
    r = await rating.get_or_create_rating(uid, "casual", player_pp=8000)
    assert r.mu == rating.starting_mu_from_pp(8000)
    assert r.sigma == rating.DUEL_TS_SIGMA0
