import asyncio
import os

import pytest

from config import settings
from services.render_farm import orders
from services.render_farm import queue as queue_module
from services.render_farm.queue import MAX_ATTEMPTS, RenderQueue, State
from services.render_farm.roster import Roster
from tests.conftest import POSTGRES
from utils.signals import Signals

pytestmark = pytest.mark.usefixtures("spare_database")

HASH = "0123456789abcdef0123456789abcdef"


async def test_a_job_offered_by_one_process_is_rendered_through_another():
    bot_side, app_side = RenderQueue(), RenderQueue()
    job = await bot_side.offer("a.osr", "t", beatmap_md5=HASH, requester=7, followed=True)
    taken = await app_side.claim("w1", "MacBook")
    assert taken.id == job.id and taken is not job
    assert (await bot_side.refresh(job)).state is State.CLAIMED and job.worker_name == "MacBook"
    assert await app_side.heartbeat(job.id, "w1", {"stage": "drawing", "done": 5, "total": 10})
    assert (await bot_side.wait(job, 0.01)).progress == {"stage": "drawing", "done": 5, "total": 10}
    assert await app_side.finish(job.id, "w1", {"path": "/tmp/v.mp4", "bytes": 3})
    await bot_side.wait(job, 0.01)
    assert job.settled.is_set() and not job.withdrawn and job.payload == {"path": "/tmp/v.mp4", "bytes": 3}
    assert await app_side.get(job.id) is None and await bot_side.open_for(7) == 0
    assert [kept.id for kept in await RenderQueue().followed()] == [job.id]
    await bot_side.close(job.id)
    assert await RenderQueue().followed() == []


async def test_a_job_nobody_follows_leaves_no_row_behind():
    line = RenderQueue()
    job = await line.offer("a.osr", "t", beatmap_md5=HASH, video_id=5)
    await line.claim("w1")
    assert await line.finish(job.id, "w1", {"video": 5})
    assert job.settled.is_set() and await RenderQueue().refresh(job) is job
    async with queue_module.RenderQueue()._factory() as session:
        from sqlalchemy import func, select

        from db.models.render_job import RenderJob

        assert await session.scalar(select(func.count()).select_from(RenderJob)) == 0


async def test_giving_up_in_one_process_is_told_to_the_one_that_follows():
    bot_side, app_side = RenderQueue(), RenderQueue()
    job = await bot_side.offer("a.osr", "t", beatmap_md5=HASH, followed=True)
    for _ in range(MAX_ATTEMPTS):
        await app_side.claim("w1", "MacBook")
        assert await app_side.give_back(job.id, "w1", "the map is nowhere")
    await bot_side.wait(job, 0.01)
    assert job.withdrawn and job.reason == "the map is nowhere" and job.attempts == MAX_ATTEMPTS
    assert job.troubles == ["MacBook: the map is nowhere"] * MAX_ATTEMPTS


async def test_a_job_whose_row_is_gone_ends_instead_of_waiting_forever():
    line = RenderQueue()
    job = await line.offer("a.osr", "t", beatmap_md5=HASH, followed=True)
    await RenderQueue().close(job.id)
    await line.wait(job, 0.01)
    assert job.settled.is_set() and job.withdrawn and job.reason == "gone"


async def test_what_nobody_collected_is_cleared_with_its_files(tmp_path):
    line = RenderQueue()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"mp4")
    workdir = tmp_path / "order-1"
    workdir.mkdir()
    job = await line.offer(str(workdir / "replay.osr"), "t", beatmap_md5=HASH, followed=True, now=0.0)
    await line.note(job.id, {"chat": 1, "status": 2, "workdir": str(workdir)})
    await line.claim("w1", now=1.0)
    assert await line.finish(job.id, "w1", {"path": str(video)})
    await line.sweep(now=queue_module.CLOSE_AFTER - 1)
    assert video.exists() and len(await line.followed()) == 1
    await line.sweep(now=queue_module.CLOSE_AFTER + 1)
    assert not video.exists() and not workdir.exists() and await line.followed() == []


class _Bot:
    def __init__(self):
        self.log = []

    async def edit_message_text(self, text, chat_id=None, message_id=None, **more):
        self.log.append(("edit", chat_id, message_id, text))

    async def delete_message(self, chat_id, message_id):
        self.log.append(("delete", chat_id, message_id))

    async def send_video(self, chat_id, video, reply_parameters=None, **more):
        self.log.append(("video", chat_id, video.path, getattr(reply_parameters, "message_id", None), more.get("caption")))


async def test_an_order_is_picked_up_after_the_bot_restarts_and_its_video_is_sent(monkeypatch, tmp_path):
    before = RenderQueue()
    workdir = tmp_path / "order-1"
    workdir.mkdir()
    job = await before.offer(str(workdir / "replay.osr"), "My play", beatmap_md5=HASH, requester=7, chat_id=-10055,
                             followed=True)
    await before.note(job.id, {"chat": -10055, "asked": 41, "status": 42, "lang": "en", "workdir": str(workdir)})

    after = RenderQueue()
    monkeypatch.setattr(orders, "queue", after)
    monkeypatch.setattr(orders, "TICK", 0.02)
    monkeypatch.setattr(orders, "_following", set())
    bot = _Bot()
    assert await orders.resume(bot) == 1
    assert await orders.resume(bot) == 0, "an order that is followed already is not followed twice"

    video = tmp_path / "v.mp4"
    video.write_bytes(b"mp4")
    app_side = RenderQueue()
    await app_side.claim("w1", "MacBook")
    assert await app_side.finish(job.id, "w1", {"path": str(video), "meta": {"duration": 12}, "bytes": 3})
    for _ in range(200):
        if not orders._following:
            break
        await asyncio.sleep(0.01)
    sent = [entry for entry in bot.log if entry[0] == "video"]
    assert sent and sent[0][1:4] == (-10055, str(video), 41) and "MacBook" in sent[0][4]
    assert ("delete", -10055, 42) in bot.log
    assert not video.exists() and not workdir.exists()
    assert await RenderQueue().followed() == []


async def test_an_order_without_a_status_message_is_dropped_on_restart(monkeypatch):
    line = RenderQueue()
    await line.offer("a.osr", "t", beatmap_md5=HASH, followed=True)
    monkeypatch.setattr(orders, "queue", line)
    monkeypatch.setattr(orders, "_following", set())
    assert await orders.resume(_Bot()) == 0
    assert await line.followed() == []


async def test_the_bot_learns_how_many_workers_are_taking_from_what_the_app_side_wrote():
    app_side, bot_side = Roster(), Roster()
    assert await bot_side.taking_anywhere() == 0
    app_side.hello("k1", "MacBook")
    app_side.hello("k2", "Tower", take=False)
    await app_side._write(app_side.taking())
    assert await bot_side.taking_anywhere() == 1
    assert await app_side.taking_anywhere() == 1


async def test_a_count_of_workers_that_is_old_counts_as_none(monkeypatch):
    from services.render_farm import roster as roster_module

    app_side, bot_side = Roster(), Roster()
    app_side.hello("k1", "MacBook")
    await app_side._write(1)
    now = roster_module.time.time()
    monkeypatch.setattr(roster_module.time, "time", lambda: now + roster_module.TOLD_FOR + 1)
    assert await bot_side.taking_anywhere() == 0


def test_orders_and_spooled_results_have_folders_that_outlive_a_restart():
    assert os.path.isabs(settings.RENDER_ORDERS_DIR) and os.path.isabs(settings.RENDER_SPOOL_DIR)


@pytest.mark.skipif(not POSTGRES, reason="PostgreSQL only")
async def test_a_channel_added_after_the_connection_is_heard_too(spare_database):
    signals = Signals(POSTGRES)
    signals.listen("render_test_one")
    try:
        assert await signals.wait(signals.mark("render_test_one"), 10.0)
        signals.listen("render_test_two")
        assert await signals.wait(signals.mark("render_test_two"), 10.0), "the new channel was not added"
        quiet = signals.mark("render_test_two")
        async with spare_database() as session:
            await signals.announce(session, "render_test_two")
            await session.commit()
        assert await signals.wait(quiet, 5.0)
    finally:
        await signals.stop()
