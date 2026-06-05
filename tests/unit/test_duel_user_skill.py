"""Unit tests for services.hps.duel_user_skill.

Each test spins up an in-memory aiosqlite database with the bare schema we
exercise (users, bounties, submissions, duel_map_pool) and seeds rows by hand.
This keeps the tests isolated from the project's real database / migrations
while still going through SQLAlchemy ORM code paths.
"""

import math
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
# Importing models registers them on Base.metadata, which create_all() needs.
from db.models.bounty import Bounty, Submission  # noqa: F401
from db.models.duel_map_pool import DuelMapPool  # noqa: F401
from db.models.user import User  # noqa: F401
from services.hps.duel_user_skill import (
    AXES,
    BOOTSTRAP_FULL_N,
    NEUTRAL_DEFAULT,
    TIME_DECAY_DAYS,
    _duel_user_pp_prior,
    _c_pen,
    compute_duel_user_skill,
    refresh_duel_user_skill,
)


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as sess:
        yield sess
    await engine.dispose()


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _add_user(session, telegram_id: int = 1, pp: int = 0) -> User:
    u = User(chat_id=-100, telegram_id=telegram_id, osu_username=f"u{telegram_id}", player_pp=pp)
    session.add(u)
    await session.flush()
    return u


async def _add_bounty(
    session,
    bounty_id: str,
    beatmap_id: int,
    star_rating: float = 5.0,
    max_combo: int = 1000,
) -> Bounty:
    b = Bounty(
        bounty_id=bounty_id,
        bounty_type="First FC",
        title=f"Bounty {bounty_id}",
        beatmap_id=beatmap_id,
        beatmap_title="x",
        star_rating=star_rating,
        drain_time=120,
        created_by=1,
        max_combo=max_combo,
    )
    session.add(b)
    await session.flush()
    return b


async def _add_submission(
    session,
    *,
    user: User,
    bounty: Bounty,
    result_type: str = "win",
    status: str = "approved",
    combo: int = 1000,
    misses: int = 0,
    days_ago: float = 1.0,
) -> Submission:
    s = Submission(
        bounty_id=bounty.bounty_id,
        user_id=user.id,
        telegram_id=user.telegram_id,
        max_combo=combo,
        misses=misses,
        status=status,
        result_type=result_type,
        submitted_at=_utcnow_naive() - timedelta(days=days_ago),
    )
    session.add(s)
    await session.flush()
    return s


async def _add_pool_map(
    session,
    beatmap_id: int,
    aim=5.0, speed=5.0, acc=5.0, cons=5.0,
    sr=None,
) -> DuelMapPool:
    # The per-axis classifier was removed — difficulty is the single objective
    # `star_rating`.  `aim` is kept as the convenient "map difficulty" knob the
    # tests pass; star_rating defaults to it when `sr` isn't given.
    star = sr if sr is not None else aim
    m = DuelMapPool(
        beatmap_id=beatmap_id, beatmapset_id=beatmap_id,
        title="t", artist="a", version="v", star_rating=star,
    )
    session.add(m)
    await session.flush()
    return m


class TestPpPrior:
    def test_zero_pp(self):
        assert _duel_user_pp_prior(0) == NEUTRAL_DEFAULT
        assert _duel_user_pp_prior(None) == NEUTRAL_DEFAULT

    def test_1000pp_equals_4(self):
        # (1000/1000)^0.6 + 3 = 1 + 3 = 4
        assert _duel_user_pp_prior(1000) == pytest.approx(4.0)

    def test_top_player_spread(self):
        # 10k and 20k PP should be noticeably different — the very thing the
        # power curve was chosen for over log10.
        ten_k = _duel_user_pp_prior(10_000)
        twenty_k = _duel_user_pp_prior(20_000)
        assert twenty_k - ten_k > 1.0
        # And both should be in [0, 10].
        assert 0.0 < ten_k < 10.0
        assert 0.0 < twenty_k < 10.0

    def test_clamped_at_10(self):
        # Very high PP should saturate at 10.
        assert _duel_user_pp_prior(10_000_000) == 10.0


class TestCpen:
    def test_fc_no_miss(self):
        assert _c_pen(1000, 1000, 0) == pytest.approx(1.0)

    def test_full_combo_with_misses(self):
        # 0.92^3 = 0.778688
        assert _c_pen(1000, 1000, 3) == pytest.approx(0.92 ** 3)

    def test_half_combo(self):
        # sqrt(0.5) ≈ 0.707
        assert _c_pen(500, 1000, 0) == pytest.approx(math.sqrt(0.5))

    def test_missing_combo_data_falls_back_to_1(self):
        # Submission may not carry combo info — penalty is only miss-based.
        assert _c_pen(None, None, 2) == pytest.approx(0.92 ** 2)

    def test_zero_max_combo_safe(self):
        # Division-by-zero guard.
        assert _c_pen(500, 0, 0) == 1.0


class TestComputeDuelUserSkill:
    @pytest.mark.asyncio
    async def test_no_submissions_returns_pp_prior(self, session):
        user = await _add_user(session, pp=2000)
        skill = await compute_duel_user_skill(user, session)
        prior = _duel_user_pp_prior(2000)
        assert skill.aim == pytest.approx(prior)
        assert skill.speed == pytest.approx(prior)
        assert skill.acc == pytest.approx(prior)
        assert skill.cons == pytest.approx(prior)
        assert skill.alpha == 0.0
        assert skill.qualifying_count == 0

    @pytest.mark.asyncio
    async def test_single_submission_blends_toward_subs(self, session):
        # Player has 1 win/condition submission → α = 0.1, mostly PP-prior.
        user = await _add_user(session, pp=1000)  # prior = 4.0
        b = await _add_bounty(session, "b1", beatmap_id=100)
        await _add_pool_map(session, 100, sr=8.0)
        await _add_submission(session, user=user, bounty=b)
        skill = await compute_duel_user_skill(user, session)

        assert skill.alpha == pytest.approx(1.0 / BOOTSTRAP_FULL_N)
        # All axes share the map's SR (8.0): blend 0.9*4 + 0.1*8 = 4.4.
        for axis in AXES:
            assert getattr(skill, axis) == pytest.approx(4.4, abs=0.05)

    @pytest.mark.asyncio
    async def test_ten_submissions_alpha_one(self, session):
        # 10 qualifying submissions → α=1, subs-only result.
        user = await _add_user(session, pp=10)  # tiny prior to make the contrast obvious
        for i in range(10):
            b = await _add_bounty(session, f"b{i}", beatmap_id=200 + i)
            await _add_pool_map(session, 200 + i, aim=7.0, speed=7.0, acc=7.0, cons=7.0)
            await _add_submission(session, user=user, bounty=b, days_ago=0.5)
        skill = await compute_duel_user_skill(user, session)
        assert skill.alpha == 1.0
        # With fresh maps all at 7.0 stars, all axes should land near 7.0.
        for axis in AXES:
            assert getattr(skill, axis) == pytest.approx(7.0, abs=0.1)

    @pytest.mark.asyncio
    async def test_old_submissions_decay(self, session):
        # Two submissions of equal map stars; one fresh, one ancient.
        # Fresh dominates the weighted average heavily.
        user = await _add_user(session, pp=10)
        b1 = await _add_bounty(session, "b1", beatmap_id=300)
        b2 = await _add_bounty(session, "b2", beatmap_id=301)
        await _add_pool_map(session, 300, aim=9.0)
        await _add_pool_map(session, 301, aim=3.0)
        await _add_submission(session, user=user, bounty=b1, days_ago=0.1)
        await _add_submission(session, user=user, bounty=b2, days_ago=89.0)
        skill = await compute_duel_user_skill(user, session)
        # Old submission's weight ≈ e^(-89/30) ≈ 0.05; fresh dominates.
        # subs-axis aim weights to ~9 (mostly from the fresh 9-star map).
        # With 2 qualifying subs α=0.2, prior=(10/1000)^0.6+3 ≈ 3.06.
        # Blend ≈ 0.8·3.06 + 0.2·9 ≈ 4.25.  The test's job is to assert that
        # the old map's weight didn't pull subs-axis down to ~6 (the naive
        # unweighted mean of 9 and 3).
        assert skill.aim == pytest.approx(4.25, abs=0.15)

    @pytest.mark.asyncio
    async def test_excludes_partial_and_unapproved(self, session):
        # partial result_type and pending status must not contribute.
        user = await _add_user(session, pp=10)
        b1 = await _add_bounty(session, "b1", beatmap_id=400)
        b2 = await _add_bounty(session, "b2", beatmap_id=401)
        b3 = await _add_bounty(session, "b3", beatmap_id=402)
        await _add_pool_map(session, 400, aim=9.0)
        await _add_pool_map(session, 401, aim=9.0)
        await _add_pool_map(session, 402, aim=9.0)
        await _add_submission(session, user=user, bounty=b1, result_type="partial")
        await _add_submission(session, user=user, bounty=b2, status="pending")
        # Only this one counts.
        await _add_submission(session, user=user, bounty=b3, result_type="condition")
        skill = await compute_duel_user_skill(user, session)
        assert skill.qualifying_count == 1

    @pytest.mark.asyncio
    async def test_excludes_out_of_window(self, session):
        # Submission older than 90 days must drop entirely.
        user = await _add_user(session, pp=10)
        b1 = await _add_bounty(session, "b1", beatmap_id=500)
        b2 = await _add_bounty(session, "b2", beatmap_id=501)
        await _add_pool_map(session, 500, aim=9.0)
        await _add_pool_map(session, 501, aim=9.0)
        await _add_submission(session, user=user, bounty=b1, days_ago=1.0)
        await _add_submission(session, user=user, bounty=b2, days_ago=100.0)
        skill = await compute_duel_user_skill(user, session)
        assert skill.qualifying_count == 1

    @pytest.mark.asyncio
    async def test_map_not_in_pool_uses_sr_fallback(self, session):
        # No duel_map_pool row → all axes use bounty.star_rating.
        user = await _add_user(session, pp=10)
        b = await _add_bounty(session, "b1", beatmap_id=999, star_rating=6.5)
        # Deliberately do not add a pool map.
        await _add_submission(session, user=user, bounty=b)
        skill = await compute_duel_user_skill(user, session)
        # α=0.1, prior=3.025 (10pp), sub axes all 6.5: blend ≈ 0.9·3.025 + 0.1·6.5 = 3.37
        for axis in AXES:
            assert getattr(skill, axis) == pytest.approx(3.37, abs=0.05)

    @pytest.mark.asyncio
    async def test_misses_reduce_weight(self, session):
        # Two equal-stars submissions; one clean, one with many misses.
        # Top-K picks the clean one first since weight × stars is bigger.
        user = await _add_user(session, pp=10)
        b_clean = await _add_bounty(session, "b1", beatmap_id=600)
        b_dirty = await _add_bounty(session, "b2", beatmap_id=601)
        await _add_pool_map(session, 600, aim=9.0)
        await _add_pool_map(session, 601, aim=5.0)
        await _add_submission(session, user=user, bounty=b_clean, misses=0)
        await _add_submission(session, user=user, bounty=b_dirty, misses=15)
        skill = await compute_duel_user_skill(user, session)
        # Both included, but clean (9-star) carries far more weight than dirty
        # (5-star with miss penalty). Subs-axis aim is closer to 9 than to 5.
        assert skill.qualifying_count == 2

    @pytest.mark.asyncio
    async def test_all_axes_share_sr(self, session):
        # Two maps at SR 9 and 1.  All four axes use star_rating, so every axis
        # weighted-averages both maps equally ≈ (9+1)/2 = 5, and the four axes
        # come out identical.  Guards against an off-by-one where we'd only
        # consider one submission per user.
        user = await _add_user(session, pp=10)
        b1 = await _add_bounty(session, "b1", beatmap_id=700)
        b2 = await _add_bounty(session, "b2", beatmap_id=701)
        await _add_pool_map(session, 700, sr=9.0)
        await _add_pool_map(session, 701, sr=1.0)
        await _add_submission(session, user=user, bounty=b1)
        await _add_submission(session, user=user, bounty=b2)
        skill = await compute_duel_user_skill(user, session)
        # α=0.2, prior≈3.025, subs≈5 → blend 0.8·3.025 + 0.2·5 = 3.42.
        for axis in AXES:
            assert getattr(skill, axis) == pytest.approx(3.42, abs=0.05)
        assert skill.aim == skill.speed == skill.acc == skill.cons


class TestRefreshDuelUserSkill:
    @pytest.mark.asyncio
    async def test_writes_back_to_user(self, session):
        user = await _add_user(session, pp=1000)
        await refresh_duel_user_skill(user, session)
        assert user.duel_user_aim == pytest.approx(4.0)
        assert user.duel_user_speed == pytest.approx(4.0)
        assert user.duel_user_acc == pytest.approx(4.0)
        assert user.duel_user_cons == pytest.approx(4.0)
        assert user.duel_skill_calculated_at is not None
