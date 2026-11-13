import asyncio
import os
import subprocess
import sys
from datetime import timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import db.models
from db.database import Base
from db.models.telegram_task import TelegramTask
from services.render_farm import telegram_rows
from services.render_farm.telegram import Absent, Asleep, Chat, Media, Sent, Thumb
from services.render_farm.telegram_rows import Carrier, Refused, Rows
from tests.conftest import POSTGRES
from utils.signals import Signals
from utils.timeutils import utcnow

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class Bot(Absent):
    def __init__(self, kept=""):
        self.log = []
        self.kept = kept
        self.broken = False

    def ready(self):
        return True

    async def is_member(self, chat_id, telegram_id):
        self.log.append(("member", chat_id, telegram_id))
        return chat_id == -1

    async def describe(self, chat_id):
        if self.broken:
            raise RuntimeError("chat not found")
        return Chat(title="Room", name="", username="room", photo=True)

    async def photo(self, chat_id):
        return b"jpeg" if chat_id == -1 else None

    async def small(self, file_id):
        return file_id.encode()

    async def local(self, file_id):
        return self.kept

    async def send_photo(self, chat_id, png, filename):
        self.log.append(("photo", chat_id, png, filename))
        return Sent(11)

    async def send_video(self, chat_id, path, filename, caption=None, width=None, height=None, duration=None):
        self.log.append(("video", chat_id, path, filename, caption, width, height, duration))
        return Sent(12, "video", Media("file", "unique", 5, 9, 1280, 720, Thumb("thumb")))

    async def pass_on(self, chat_id, kind, file_id, caption=None):
        self.log.append(("pass", chat_id, kind, file_id, caption))
        return Sent(13)


@pytest_asyncio.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rows.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def pair(factory):
    signals = Signals()
    bot = Bot()
    told = []

    async def linked(telegram_id, name, where):
        told.append((telegram_id, name, where))

    carrier = Carrier(factory, bot, signals, linked=linked)
    stop = asyncio.Event()
    running = asyncio.create_task(carrier.run(stop))
    yield Rows(factory, signals), bot, carrier, told
    stop.set()
    await running


async def _left(factory) -> int:
    async with factory() as session:
        return await session.scalar(select(func.count()).select_from(TelegramTask))


async def test_every_call_crosses_the_table_and_comes_back(pair, factory):
    rows, bot, _, _ = pair
    assert await rows.is_member(-1, 7) is True
    assert await rows.is_member(-2, 7) is False
    assert await rows.describe(-1) == Chat(title="Room", name="", username="room", photo=True)
    assert await rows.photo(-1) == b"jpeg"
    assert await rows.photo(-2) is None
    assert await rows.small("abc") == b"abc"
    assert await rows.send_photo(-1, b"\x89PNG", "score.png") == Sent(11)
    sent = await rows.send_video(-1, "/tmp/render.mp4", "render.mp4", caption="hi", width=1280, height=720, duration=9)
    assert sent == Sent(12, "video", Media("file", "unique", 5, 9, 1280, 720, Thumb("thumb")))
    assert await rows.pass_on(7, "animation", "file", "from a friend") == Sent(13)
    assert ("photo", -1, b"\x89PNG", "score.png") in bot.log
    assert ("video", -1, "/tmp/render.mp4", "render.mp4", "hi", 1280, 720, 9) in bot.log
    assert ("pass", 7, "animation", "file", "from a friend") in bot.log
    assert await _left(factory) == 0


async def test_membership_is_remembered_for_a_while(pair):
    rows, bot, _, _ = pair
    assert await rows.is_member(-1, 7) and await rows.is_member(-1, 7)
    assert bot.log.count(("member", -1, 7)) == 1
    rows.forget()
    assert await rows.is_member(-1, 7)
    assert bot.log.count(("member", -1, 7)) == 2


async def test_what_telegram_refused_is_told_and_not_tried_again(pair, factory):
    rows, bot, _, _ = pair
    bot.broken = True
    with pytest.raises(Refused, match="chat not found"):
        await rows.describe(-1)
    assert await _left(factory) == 0
    assert rows.ready()


async def test_with_no_bot_the_port_says_asleep_and_leaves_nothing_to_send_late(factory, monkeypatch):
    monkeypatch.setattr(telegram_rows, "CLAIM_WITHIN", 0.2)
    monkeypatch.setattr(telegram_rows, "POLL", 0.05)
    rows = Rows(factory, Signals())
    assert rows.ready()
    with pytest.raises(Asleep):
        await rows.send_photo(-1, b"png", "score.png")
    assert not rows.ready()
    assert await _left(factory) == 0
    with pytest.raises(Asleep):
        await rows.describe(-1)
    assert await rows.is_member(-1, 7) is False


async def test_a_request_nobody_waits_for_is_not_carried_out(factory):
    bot = Bot()
    carrier = Carrier(factory, bot, Signals())
    now = utcnow()
    async with factory() as session:
        session.add(TelegramTask(kind="send_photo", payload={"chat": -1, "filename": "a.png"}, blob=b"png",
                                 next_attempt_at=now - timedelta(minutes=2), created_at=now - timedelta(minutes=2),
                                 expires_at=now - timedelta(minutes=1)))
        await session.commit()
    assert await carrier.once() == 0
    assert bot.log == []
    assert await carrier.sweep(now + timedelta(hours=2)) == 1


async def test_a_video_is_read_from_the_path_the_bot_names_and_removed_after_the_last_reader(pair, tmp_path):
    rows, bot, _, _ = pair
    kept = tmp_path / "video.mp4"
    kept.write_bytes(b"0123456789")
    bot.kept = str(kept)
    first = await rows.fetch("file", "unique")
    second = await rows.fetch("file", "unique")
    assert first.size == 10
    assert b"".join([chunk async for chunk in first.chunks(4)]) == b"0123456789"
    await first.release()
    assert kept.exists()
    await second.release()
    assert not kept.exists()


async def test_the_notice_after_a_sign_in_waits_for_the_bot_and_is_tried_again(factory):
    signals = Signals()
    rows = Rows(factory, signals)
    seen = []

    async def linked(telegram_id, name, where):
        seen.append((telegram_id, name, where))
        if len(seen) == 1:
            raise RuntimeError("telegram is away")

    await rows.linked(42, "peppy", (-100, 7))
    await rows.linked(43, "cookiezi", None)
    assert await _left(factory) == 2
    carrier = Carrier(factory, Bot(), signals, linked=linked)
    now = utcnow()
    assert await carrier.box.sweep(factory, now) == 1
    assert await carrier.box.sweep(factory, now + timedelta(seconds=1)) == 0
    assert await carrier.box.sweep(factory, now + timedelta(seconds=3)) == 1
    assert seen == [(42, "peppy", (-100, 7)), (43, "cookiezi", None), (42, "peppy", (-100, 7))]
    assert await carrier.sweep(now + timedelta(hours=2)) == 2


CHILD = """
import asyncio, sys
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from services.render_farm.telegram import Absent, Sent
from services.render_farm.telegram_rows import Carrier
from utils.signals import Signals

class Bot(Absent):
    async def send_photo(self, chat_id, png, filename):
        with open(sys.argv[2], "wb") as told:
            told.write(str(chat_id).encode() + b" " + filename.encode() + b" " + png)
        return Sent(77)

async def main():
    engine = create_async_engine("sqlite+aiosqlite:///" + sys.argv[1], poolclass=NullPool)
    carrier = Carrier(async_sessionmaker(engine, expire_on_commit=False), Bot(), Signals(), idle=0.05)
    open(sys.argv[3], "w").close()
    await carrier.run(asyncio.Event())

asyncio.run(main())
"""


@pytest.mark.sqlite_only
async def test_a_card_asked_in_one_process_is_sent_by_another(factory, tmp_path, monkeypatch):
    monkeypatch.setattr(telegram_rows, "POLL", 0.05)
    told, up = tmp_path / "told", tmp_path / "up"
    child = subprocess.Popen([sys.executable, "-c", CHILD, str(tmp_path / "rows.db"), str(told), str(up)], cwd=ROOT)
    try:
        for _ in range(200):
            if up.exists():
                break
            assert child.poll() is None, "the other process ended early"
            await asyncio.sleep(0.05)
        assert up.exists()
        sent = await Rows(factory, Signals()).send_photo(-5, b"\x89PNG card", "score.png")
    finally:
        child.terminate()
        child.wait(timeout=10)
    assert sent == Sent(77)
    assert told.read_bytes() == b"-5 score.png \x89PNG card"


@pytest.mark.skipif(not POSTGRES, reason="PostgreSQL only")
async def test_a_notify_wakes_a_listener_on_another_connection(factory):
    signals = Signals(POSTGRES)
    signals.listen("telegram_test")
    try:
        assert await signals.wait(signals.mark("telegram_test"), 10.0), "the listener did not connect"
        quiet = signals.mark("telegram_test")
        assert not await signals.wait(quiet, 0.3)
        async with factory() as session:
            await signals.announce(session, "telegram_test")
            assert not quiet.is_set(), "a notify is not heard before its commit"
            await session.commit()
        assert await signals.wait(quiet, 5.0)
    finally:
        await signals.stop()
