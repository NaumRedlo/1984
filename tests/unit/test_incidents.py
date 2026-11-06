import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from db.database import Base
from bot.handlers.admin import incidents as admin_incidents
from services import incidents
from services.render_farm import orders
from services.render_farm.queue import RenderQueue

HASH = "0123456789abcdef0123456789abcdef"

def _message(chat_type="supergroup"):
    return SimpleNamespace(document=SimpleNamespace(file_size=100, file_name="a.osr"), from_user=SimpleNamespace(id=7),
                           chat=SimpleNamespace(id=-10055, type=chat_type), reply=AsyncMock())

@pytest.mark.asyncio
async def test_a_noted_incident_gets_a_short_code_and_keeps_its_trace():
    try:
        raise RuntimeError("the mirror said no")
    except RuntimeError as exc:
        code = await incidents.note("farm", "download", "RuntimeError: the mirror said no", telegram_id=7, detail={"size": 3}, error=exc)
    assert len(code) == incidents.CODE_LENGTH and set(code) <= set(incidents.LETTERS)
    found = await incidents.find(code.lower())
    assert found.stage == "download" and found.telegram_id == 7
    assert "the mirror said no" in found.trace and '"size": 3' in found.detail
    assert [one.code for one in await incidents.recent("farm")] == [code]
    assert await incidents.recent("bot") == []

@pytest.mark.asyncio
async def test_incidents_are_saved_in_the_database_and_old_ones_are_dropped(monkeypatch, tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'incidents.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(incidents, "AsyncSessionFactory", async_sessionmaker(engine, expire_on_commit=False))
    monkeypatch.setattr(incidents, "saving", True)
    first = await incidents.note("farm", "header", "unreadable")
    incidents.forget()
    assert (await incidents.find(first)).reason == "unreadable"
    from datetime import timedelta
    from sqlalchemy import update
    from db.models.incident import Incident
    from utils.timeutils import utcnow
    async with incidents.AsyncSessionFactory() as session:
        await session.execute(update(Incident).where(Incident.code == first).values(created_at=utcnow() - timedelta(days=31)))
        await session.commit()
    second = await incidents.note("bot", "handler", "ValueError: x")
    incidents.forget()
    assert await incidents.find(first) is None
    assert [one.code for one in await incidents.recent()] == [second]
    await engine.dispose()

@pytest.mark.asyncio
async def test_an_unreadable_replay_is_noted_and_its_code_is_told(monkeypatch, tmp_path):
    monkeypatch.setattr(orders, "queue", RenderQueue())
    monkeypatch.setattr(orders.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)))
    class Factory:
        async def __aenter__(self):
            return session
        async def __aexit__(self, *exc):
            return False
    monkeypatch.setattr(orders, "AsyncSessionFactory", lambda: Factory())
    async def download(document, destination):
        with open(destination, "wb") as out:
            out.write(b"definitely not a replay")
    message = _message()
    await orders.take(SimpleNamespace(download=download), message, "ru")
    noted, = await incidents.recent("farm")
    assert noted.stage == "header" and noted.telegram_id == 7 and noted.chat_id == -10055
    assert '"file": "a.osr"' in noted.detail and b"definitely".hex() in noted.detail
    said = message.reply.await_args.args[0]
    assert said.startswith("Не получилось открыть этот реплей.") and noted.code in said

@pytest.mark.asyncio
async def test_a_failed_download_and_a_broken_order_are_noted_with_their_trace(monkeypatch, tmp_path):
    monkeypatch.setattr(orders, "queue", RenderQueue())
    monkeypatch.setattr(orders.tempfile, "mkdtemp", lambda **kwargs: str(tmp_path))
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)))
    class Factory:
        async def __aenter__(self):
            return session
        async def __aexit__(self, *exc):
            return False
    monkeypatch.setattr(orders, "AsyncSessionFactory", lambda: Factory())
    async def download(document, destination):
        raise ConnectionError("telegram is away")
    message = _message()
    await orders.take(SimpleNamespace(download=download), message, "en")
    noted, = await incidents.recent("farm")
    assert noted.stage == "download" and "telegram is away" in noted.trace

    monkeypatch.setattr(orders.members, "shares_a_group", AsyncMock(side_effect=KeyError("members")))
    message = _message("private")
    await orders.take(SimpleNamespace(download=download), message, "en")
    broken = (await incidents.recent("farm"))[0]
    assert broken.stage == "order" and "KeyError" in broken.reason and "members" in broken.trace
    said = message.reply.await_args.args[0]
    assert said.startswith("Something broke") and broken.code in said

@pytest.mark.asyncio
async def test_a_worker_giving_a_job_back_is_noted_and_kept_in_the_jobs_story():
    line = RenderQueue()
    job = line.offer("a.osr", "song", beatmap_md5=HASH, requester=7, chat_id=-1)
    line.claim("worker-key", "Laptop")
    assert line.give_back(job.id, "worker-key", "map download failed")
    await asyncio.sleep(0)
    noted, = await incidents.recent("farm")
    assert (noted.stage, noted.reason, noted.telegram_id) == ("worker", "map download failed", 7)
    assert '"worker": "Laptop"' in noted.detail
    assert job.troubles == ["Laptop: map download failed"]
    assert "Laptop: map download failed" in str(orders._story(job)["troubles"])

@pytest.mark.asyncio
async def test_the_admin_sees_the_list_and_one_incident_in_full():
    try:
        raise OSError("disk full")
    except OSError as exc:
        code = await incidents.note("farm", "store", "OSError: disk full", telegram_id=7, chat_id=-1, detail={"job": "j1"}, error=exc)
    await incidents.note("bot", "handler", "ValueError: <bad>")
    listed = SimpleNamespace(answer=AsyncMock())
    await admin_incidents.cmd_incidents(listed, SimpleNamespace(args="farm"))
    text = listed.answer.await_args.args[0]
    assert code in text and "ValueError" not in text
    whole = SimpleNamespace(answer=AsyncMock())
    await admin_incidents.cmd_incident(whole, SimpleNamespace(args=code.lower()))
    text = whole.answer.await_args.args[0]
    assert "disk full" in text and "whois 7" in text and '"job": "j1"' in text and len(text) <= 4096
    every = SimpleNamespace(answer=AsyncMock())
    await admin_incidents.cmd_incidents(every, SimpleNamespace(args=None))
    assert "ValueError: &lt;bad&gt;" in every.answer.await_args.args[0]
