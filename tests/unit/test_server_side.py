import asyncio
import socket

import aiohttp
import pytest

from config import settings
from db.setup import prepare
from server import side as side_module
from server.side import AppSide
from services.oauth import server as oauth
from services.render_farm import http as farm_http
from services.render_farm.telegram import Absent
from tasks import duels as duel_tasks
from tests.conftest import POSTGRES

pytestmark = pytest.mark.usefixtures("spare_database")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def quiet_loops(monkeypatch):
    began = []

    async def forever(*args, **kwargs):
        began.append(args)
        await asyncio.Event().wait()

    class Tracker:
        def __init__(self, osu):
            self.osu = osu

        async def run(self, shutdown):
            began.append(("tracker",))
            await asyncio.Event().wait()

    monkeypatch.setattr(side_module, "periodic_profile_updates", forever)
    monkeypatch.setattr(side_module, "check_maps", forever)
    monkeypatch.setattr(side_module, "run_duels", forever)
    monkeypatch.setattr(side_module, "LiveTracker", Tracker)
    return began


async def test_the_app_side_serves_and_runs_its_loops_with_no_bot_and_stops_clean(quiet_loops, spare_database, monkeypatch):
    monkeypatch.setattr(oauth, "get_db_session", spare_database)
    monkeypatch.setattr(farm_http, "RENDER_WORKER_TOKEN", "a-shared-secret")
    port = _free_port()
    told = []

    async def linked(telegram_id, name, where):
        told.append(telegram_id)

    side = AppSide(object(), asyncio.Event(), port=port)
    await side.start(Absent(), linked)
    await asyncio.sleep(0.05)
    assert len(quiet_loops) == 4 and len(side.tasks) == 5
    async with aiohttp.ClientSession() as client:
        async with client.get(f"http://127.0.0.1:{port}/health") as reply:
            assert reply.status == 200
            assert await reply.json() == {"ok": True, "database": True, "telegram": False}
        async with client.get(f"http://127.0.0.1:{port}/render/hello") as reply:
            assert reply.status == 401
    assert oauth._linked is linked
    await side.stop()
    assert side.tasks == [] and oauth._linked is None and not farm_http.telegram_ready()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()


async def test_health_says_so_when_the_database_does_not_answer(monkeypatch):
    class Broken:
        async def __aenter__(self):
            raise ConnectionError("no database")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(oauth, "get_db_session", lambda: Broken())
    reply = await oauth.health(None)
    assert reply.status == 503 and b'"database": false' in reply.body


def test_the_server_needs_no_bot_token_and_the_bot_does(monkeypatch):
    for name, value in (("TELEGRAM_BOT_TOKEN", ""), ("OSU_CLIENT_ID", "1"), ("OSU_CLIENT_SECRET", "s"),
                        ("OAUTH_ENCRYPTION_KEY", "k"), ("ADMIN_IDS", [1])):
        monkeypatch.setattr(settings, name, value)
    settings.validate_settings(telegram=False)
    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        settings.validate_settings()


async def test_tables_can_be_prepared_by_two_processes_at_once(spare_database):
    engine = spare_database.kw["bind"]
    await asyncio.gather(prepare(engine), prepare(engine)) if POSTGRES else await prepare(engine)
    await prepare(engine)


async def test_duel_notices_are_delivered_by_the_bot_until_it_stops(monkeypatch):
    from services.duels import notify

    calls = []
    stop = asyncio.Event()

    async def deliver(factory, send, language):
        calls.append(send)
        if len(calls) == 2:
            stop.set()
        return 0

    class Bot:
        async def send_message(self, chat_id, text, **more):
            pass

    monkeypatch.setattr(notify, "deliver", deliver)
    monkeypatch.setattr(duel_tasks, "EVERY", 0.01)
    await asyncio.wait_for(duel_tasks.deliver_notices(stop, Bot()), 2.0)
    assert len(calls) == 2 and callable(calls[0])


def test_the_server_logs_into_files_of_its_own():
    import subprocess
    import sys

    told = subprocess.run(
        [sys.executable, "-c", "import os, tempfile; os.chdir(tempfile.mkdtemp()); import server.main, utils.logger as l; "
                               "print(l.LOG_FILE_LEVEL, l.LOG_ERROR_LEVEL)"],
        capture_output=True, text=True, check=True, cwd=settings.PROJECT_ROOT, env={**__import__("os").environ, "PYTHONPATH": settings.PROJECT_ROOT},
    ).stdout.split()
    assert told == ["server.log", "server-errors.log"]
