from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
from db.migrations.scope_best_score_ids import run_scope_best_score_ids_migration
from db.models.map_attempt import UserMapAttempt
from db.models.user import User
from utils.osu.api_client import OsuApiClient


async def test_legacy_attempt_constraint_is_migrated_without_losing_rows():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("CREATE UNIQUE INDEX legacy_attempt_score_id ON user_map_attempts(score_id)"))
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            session.add_all([
                User(chat_id=-1, telegram_id=1, osu_user_id=10, osu_username="player"),
                User(chat_id=-2, telegram_id=2, osu_user_id=10, osu_username="player"),
            ])
            await session.commit()
            users = (await session.execute(select(User).order_by(User.id))).scalars().all()
            session.add(UserMapAttempt(user_id=users[0].id, score_id=123, beatmap_id=9, pp=100))
            await session.commit()
            other_id = users[1].id
        await run_scope_best_score_ids_migration(engine)
        await run_scope_best_score_ids_migration(engine)
        async with factory() as session:
            session.add(UserMapAttempt(user_id=other_id, score_id=123, beatmap_id=9, pp=100))
            await session.commit()
            attempts = (await session.execute(select(UserMapAttempt).order_by(UserMapAttempt.user_id))).scalars().all()
            assert [(row.user_id, row.score_id) for row in attempts] == [(users[0].id, 123), (other_id, 123)]
    finally:
        await engine.dispose()


async def test_same_osu_attempt_syncs_for_two_chat_memberships():
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
            users = (await session.execute(select(User).order_by(User.id))).scalars().all()
            class Client:
                async def effective_sr(self, *_args):
                    return None
                async def _fill_ranked_dates_quietly(self, session, _user_id):
                    await session.flush()
            client = Client()
            raw = {"id": 123, "pp": 100, "beatmap": {"id": 9, "status": "pending"}, "beatmapset": {"id": 8}}
            for user in users:
                assert await OsuApiClient.sync_user_map_attempts(client, user, session, [raw, raw]) == 2
                await session.commit()
            attempts = (await session.execute(select(UserMapAttempt).order_by(UserMapAttempt.user_id))).scalars().all()
            assert [(row.user_id, row.score_id) for row in attempts] == [(users[0].id, 123), (users[1].id, 123)]
    finally:
        await engine.dispose()
