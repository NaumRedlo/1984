from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from db.database import Base
from db.models.billing import BillingSubscription
from db.models.player import Player
from services.render_farm import billing, subscriptions
from utils.timeutils import utcnow

@pytest.fixture
async def factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    made = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(billing, "AsyncSessionFactory", made)
    monkeypatch.setattr(subscriptions, "AsyncSessionFactory", made)
    async def prices():
        return ()
    monkeypatch.setattr(billing, "_prices", prices)
    yield made
    await engine.dispose()

def paid(player_id: int) -> BillingSubscription:
    return BillingSubscription(id=f"s{player_id}", player_id=player_id, plan_key="lava:offer", title="Lava", name="Lava", amount="199",
                               currency="RUB", periodicity="MONTHLY", email="a@b.c", state="active", paid_until=utcnow() + timedelta(days=20))

@pytest.mark.asyncio
async def test_the_subscription_is_found_on_the_player_the_app_pays_for(factory):
    async with factory() as session:
        session.add_all([Player(id=5, osu_user_id=111, osu_username="one", telegram_id=None), Player(id=9, osu_user_id=222, osu_username="two", telegram_id=77)])
        await session.flush()
        session.add(paid(9))
        await session.commit()
    player_id, held = await billing.held_for(77, None, 111)
    assert player_id == 9 and held["active"] and held["name"] == "Lava"
    assert await billing.held_of(None, 111) is None

@pytest.mark.asyncio
async def test_someone_elses_player_with_the_same_osu_account_is_not_borrowed(factory):
    async with factory() as session:
        session.add(Player(id=3, osu_user_id=111, osu_username="three", telegram_id=55))
        await session.flush()
        session.add(paid(3))
        await session.commit()
    player_id, held = await billing.held_for(77, None, 111)
    assert player_id is None and held is None

@pytest.mark.asyncio
async def test_an_osu_account_proven_by_oauth_brings_its_subscription_whatever_telegram_the_player_names(factory):
    async with factory() as session:
        session.add(Player(id=3, osu_user_id=111, osu_username="three", telegram_id=55))
        await session.flush()
        session.add(paid(3))
        await session.commit()
    player_id, held = await billing.held_for(77, None, 111, owns_osu=True)
    assert player_id == 3 and held["active"]
