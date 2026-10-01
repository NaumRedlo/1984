from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
from db.models.best_score import UserBestScore
from db.models.user import User
from utils.osu.api_client import OsuApiClient


async def test_the_same_person_in_two_chats_keeps_one_set_of_best_scores():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add_all([
                User(chat_id=-1, telegram_id=1, osu_user_id=10, osu_username="player"),
                User(chat_id=-2, telegram_id=1, osu_user_id=10, osu_username="player"),
            ])
            await session.commit()
            users = (await session.execute(select(User).order_by(User.id))).scalars().all()
            assert users[0].player_id is not None and users[0].player_id == users[1].player_id

            class Client:
                async def get_user_best_scores(self, *_args, **_kwargs):
                    raw = {"id": 123, "pp": 100, "beatmap": {"id": 9, "status": "pending"}, "beatmapset": {"id": 8}}
                    return [raw, raw]

                async def effective_sr(self, *_args):
                    return None

                async def _fill_ranked_dates_quietly(self, session, _player_id):
                    await session.flush()

            client = Client()
            for user in users:
                assert await OsuApiClient.sync_user_best_scores(client, user, session)
                await session.commit()
            scores = (await session.execute(select(UserBestScore))).scalars().all()
            assert [(score.player_id, score.score_id) for score in scores] == [(users[0].player_id, 123)]
            assert users[0].best_scores_baseline_at is not None
            assert users[1].best_scores_baseline_at == users[0].best_scores_baseline_at
    finally:
        await engine.dispose()


async def test_a_row_that_belongs_to_no_player_syncs_nothing():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add_all([
                User(chat_id=-1, telegram_id=1, osu_user_id=10, osu_username="player"),
                User(chat_id=-2, telegram_id=2, osu_user_id=10, osu_username="player"),
            ])
            await session.commit()
            apart = (await session.execute(select(User).where(User.telegram_id == 2))).scalar_one()
            assert apart.player_id is None

            class Client:
                async def get_user_best_scores(self, *_args, **_kwargs):
                    raise AssertionError("osu! was asked for a row without a player")

            assert not await OsuApiClient.sync_user_best_scores(Client(), apart, session)
            assert await OsuApiClient.sync_user_map_attempts(Client(), apart, session, [{"id": 1, "beatmap": {"id": 2}}]) == 0
    finally:
        await engine.dispose()
