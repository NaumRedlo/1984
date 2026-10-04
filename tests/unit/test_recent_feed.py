import asyncio
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from bot.handlers.profile import recent as handler
from db.models.map_attempt import UserMapAttempt
from db.models.user import User
from db.models.witnessed_play import WitnessedPlay
from services import recent_plays
from services.render_farm import community, invites
from tasks.live_tracker import LiveTracker
from tests.unit.test_live_tracker import database, fresh_presence
from utils.osu.api_client import OsuApiClient

AT = datetime(2026, 10, 5, 12)


def score(score_id=123):
    return {"id": score_id, "ended_at": AT.isoformat(), "score": 100000,
            "max_combo": 500, "pp": 100, "accuracy": 0.99, "passed": True, "rank": "S",
            "mods": ["HD", "CL"], "statistics": {"count_300": 300, "count_100": 2, "count_50": 0, "count_miss": 0},
            "beatmap": {"id": 100, "difficulty_rating": 5, "status": "ranked"},
            "beatmapset": {"id": 200, "artist": "artist", "title": "map"}}


class Client:
    def __init__(self):
        self.scores = [score()]
        self.synced = []

    async def get_user_recent_scores(self, *_args, **kwargs):
        return self.scores if kwargs.get("mode") == "osu" else []

    async def sync_user_map_attempts(self, user, session, scores):
        self.synced.append([raw["id"] for raw in scores])
        return await OsuApiClient.sync_user_map_attempts(self, user, session, scores)

    async def effective_sr(self, *_args):
        return None

    async def _fill_ranked_dates_quietly(self, session, _player_id):
        await session.flush()


async def setup(database, monkeypatch, *, linked=True):
    async with database() as session:
        user = User(chat_id=-1, telegram_id=7, osu_user_id=70, osu_username="Naum", cover_url="cover")
        session.add(user)
        await session.commit()
    if linked:
        invites.remember("mac", invites.Owner(7, "Naum", user.player_id))
    wait = SimpleNamespace(edit_text=AsyncMock(), delete=AsyncMock())
    message = SimpleNamespace(from_user=SimpleNamespace(id=7, first_name="Naum", username="naum"),
        answer=AsyncMock(return_value=wait), answer_photo=AsyncMock(return_value=SimpleNamespace(chat=SimpleNamespace(id=-1), message_id=42)))
    monkeypatch.setattr(handler, "get_db_session", database)
    monkeypatch.setattr(handler, "get_language", AsyncMock(return_value="en"))
    monkeypatch.setattr(handler, "get_valid_token", AsyncMock(return_value=None))
    monkeypatch.setattr(handler, "get_real_reply", lambda _message: None)
    monkeypatch.setattr(handler, "require_registered_user", AsyncMock(return_value=user))
    monkeypatch.setattr(handler, "build_recent_card_data", AsyncMock(return_value={"beatmap_id": 100}))
    monkeypatch.setattr(handler.card_renderer, "generate_recent_card_async", AsyncMock(side_effect=lambda _data: BytesIO(b"png")))
    monkeypatch.setattr(handler, "remember_message_context", lambda *_args: None)
    evaluated = AsyncMock(return_value=[])
    monkeypatch.setattr(handler, "evaluate_recent_plays", evaluated)
    import utils.title_progress
    monkeypatch.setattr(utils.title_progress, "evaluate_recent_plays", evaluated)
    return user, Client(), message, evaluated


async def rs(message, client, chat=-1):
    await handler.cmd_recent(message, SimpleNamespace(args="osu"), client, tenant_chat_id=chat)


@pytest.mark.parametrize("first", ["rs", "tracker"])
async def test_rs_and_tracker_share_one_result_in_either_order(database, monkeypatch, first):
    user, client, message, evaluated = await setup(database, monkeypatch)
    tracker = LiveTracker(client)
    if first == "rs":
        await rs(message, client)
        assert await tracker.catch(70) == 0
    else:
        assert await tracker.catch(70) == 1
        await rs(message, client)
    await rs(message, client)
    assert client.synced == [[123]]
    evaluated.assert_awaited_once()
    assert message.answer_photo.await_count == 2
    async with database() as session:
        attempts = (await session.execute(select(UserMapAttempt))).scalars().all()
        assert [(row.player_id, row.score_id) for row in attempts] == [(user.player_id, 123)]
        feed = await community.gather(session, -1, 7, now=AT)
        assert len(feed["live"]) == 1


async def test_rs_preserves_the_usual_behavior_without_dossier_authorization(database, monkeypatch):
    _, client, message, evaluated = await setup(database, monkeypatch, linked=False)
    await rs(message, client)
    await rs(message, client)
    assert await LiveTracker(client).catch(70) == 1
    assert client.synced == [[123], [123], [123]]
    assert evaluated.await_count == 3
    assert message.answer_photo.await_count == 2


async def test_rs_in_two_chats_uses_the_same_dossier_history(database, monkeypatch):
    user, client, message, evaluated = await setup(database, monkeypatch)
    async with database() as session:
        session.add(User(chat_id=-2, telegram_id=7, osu_user_id=70, osu_username="Naum", cover_url="cover"))
        await session.commit()
    await rs(message, client)
    await rs(message, client, chat=-2)
    assert client.synced == [[123]]
    evaluated.assert_awaited_once()
    assert message.answer_photo.await_count == 2
    async with database() as session:
        attempts = (await session.execute(select(UserMapAttempt))).scalars().all()
        assert [(row.player_id, row.score_id) for row in attempts] == [(user.player_id, 123)]


async def test_revoking_the_dossier_link_restores_the_regular_rs_behavior(database, monkeypatch):
    _, client, message, evaluated = await setup(database, monkeypatch)
    await rs(message, client)
    invites.forget(invites.digest("mac"))
    await rs(message, client)
    assert client.synced == [[123], [123]]
    assert evaluated.await_count == 2


async def test_rs_still_answers_but_does_not_copy_a_witness_play(database, monkeypatch):
    user, client, message, evaluated = await setup(database, monkeypatch)
    async with database() as session:
        session.add(WitnessedPlay(player_id=user.player_id, replay_hash="a" * 32, beatmap_md5="b" * 32,
            beatmap_id=100, played_at=AT, created_at=AT, score=100000, max_combo=500,
            count_100=2, count_50=0, count_miss=0, passed=True, mods="HD"))
        await session.commit()
    await rs(message, client)
    assert await LiveTracker(client).catch(70) == 0
    assert client.synced == []
    evaluated.assert_not_awaited()
    message.answer_photo.assert_awaited_once()
    async with database() as session:
        assert (await session.execute(select(UserMapAttempt))).scalars().all() == []
        feed = await community.gather(session, -1, 7, now=AT)
        assert len(feed["live"]) == 1


async def test_simultaneous_rs_and_tracking_keep_new_results_once(database, monkeypatch):
    _, client, message, evaluated = await setup(database, monkeypatch)
    entered, release = asyncio.Event(), asyncio.Event()
    original = client.sync_user_map_attempts
    async def slow_sync(user, session, scores):
        entered.set()
        await release.wait()
        return await original(user, session, scores)
    client.sync_user_map_attempts = slow_sync
    command = asyncio.create_task(rs(message, client))
    await asyncio.wait_for(entered.wait(), 2)
    tracking = asyncio.create_task(LiveTracker(client).catch(70))
    await asyncio.sleep(0)
    release.set()
    _, caught = await asyncio.wait_for(asyncio.gather(command, tracking), 2)
    assert caught == 0
    assert client.synced == [[123]]
    evaluated.assert_awaited_once()
    client.scores = [score(124), score(124), score()]
    assert await LiveTracker(client).catch(70) == 1
    await rs(message, client)
    assert client.synced == [[123], [124]]
    async with database() as session:
        assert list((await session.execute(select(UserMapAttempt.score_id).order_by(UserMapAttempt.score_id))).scalars()) == [123, 124]


def test_dossier_link_detection_supports_telegram_and_standalone_accounts():
    user = SimpleNamespace(player_id=5, telegram_id=7)
    assert not recent_plays.is_linked(user)
    invites.remember("old", invites.Owner(7, "Naum"))
    assert recent_plays.is_linked(user)
    invites.forget(invites.digest("old"))
    assert not recent_plays.is_linked(user)
    invites.remember("new", invites.Owner(0, "Naum", 5))
    assert recent_plays.is_linked(SimpleNamespace(player_id=5, telegram_id=None))
    assert not recent_plays.is_linked(SimpleNamespace(player_id=6, telegram_id=None))
