from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
from db.models.map_attempt import UserMapAttempt
from db.models.user import User
from utils.osu.api_client import OsuApiClient


async def test_the_same_person_in_two_chats_keeps_one_set_of_attempts():
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

            class Client:
                async def effective_sr(self, *_args):
                    return None

                async def _fill_ranked_dates_quietly(self, session, _player_id):
                    await session.flush()

            client = Client()
            raw = {"id": 123, "pp": 100, "beatmap": {"id": 9, "status": "pending"}, "beatmapset": {"id": 8}}
            for user in users:
                assert await OsuApiClient.sync_user_map_attempts(client, user, session, [raw, raw]) == 2
                await session.commit()
            attempts = (await session.execute(select(UserMapAttempt))).scalars().all()
            assert [(row.player_id, row.score_id) for row in attempts] == [(users[0].player_id, 123)]
    finally:
        await engine.dispose()
