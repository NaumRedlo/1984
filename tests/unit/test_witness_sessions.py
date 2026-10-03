from datetime import timedelta, timezone

from sqlalchemy import select

from db.models.player import Player
from db.models.user import User
from db.models.witness_session import WitnessSession
from services.render_farm import witness_sessions as sessions
from tests.unit.test_render_farm_community import NOW, _seed, factory, served

def _unix(moment):
    return int(moment.replace(tzinfo=timezone.utc).timestamp())

def _told(**kw):
    base = dict(started_at=_unix(NOW - timedelta(hours=2)), ended_at=_unix(NOW - timedelta(minutes=1)), play_seconds=3600, plays=14, time_zone="Europe/Moscow")
    base.update(kw)
    return base

def test_a_time_zone_is_believed_only_when_it_names_a_real_one():
    assert sessions.zone_of("Europe/Moscow") == "Europe/Moscow"
    assert sessions.zone_of(" Asia/Tokyo ") == "Asia/Tokyo"
    assert sessions.zone_of("UTC") == "UTC"
    assert sessions.zone_of("America/Argentina/Buenos_Aires") == "America/Argentina/Buenos_Aires"
    assert sessions.zone_of("Mars/Olympus") is None
    assert sessions.zone_of("../../etc/passwd") is None
    assert sessions.zone_of("") is None
    assert sessions.zone_of(5) is None
    assert sessions.zone_of("A" * 80) is None

def test_a_session_is_read_only_when_it_could_have_happened():
    assert sessions.read(_told(), NOW) is not None
    assert sessions.read(_told(started_at=_unix(NOW - timedelta(days=10, hours=1)), ended_at=_unix(NOW - timedelta(days=10))), NOW) is not None
    assert sessions.read(_told(started_at=_unix(NOW - timedelta(days=17, hours=1)), ended_at=_unix(NOW - timedelta(days=17))), NOW) is None
    assert sessions.read(_told(ended_at=_unix(NOW - timedelta(hours=3))), NOW) is None
    assert sessions.read(_told(ended_at=_unix(NOW + timedelta(hours=1))), NOW) is None
    assert sessions.read(_told(started_at=_unix(NOW - timedelta(days=9)), ended_at=_unix(NOW)), NOW) is None
    assert sessions.read(_told(play_seconds=-1), NOW) is None
    assert sessions.read(_told(plays=10 ** 6), NOW) is None
    assert sessions.read(_told(started_at="yesterday"), NOW) is None
    assert sessions.read(_told(started_at=True), NOW) is None
    assert sessions.read(_told(play_seconds=100_000), NOW) is None
    assert sessions.read(_told(play_seconds=7200 + 30), NOW) is None
    assert sessions.read(_told(play_seconds=7190), NOW)["play_seconds"] == 7140

async def _player(factory, user_id):
    async with factory() as s:
        return (await s.get(User, user_id)).player_id

async def test_a_session_is_kept_once_and_grows_as_it_is_told_again(factory):
    naum, _, _ = await _seed(factory)
    pid = await _player(factory, naum)
    async with factory() as s:
        player = await s.get(Player, pid)
        assert await sessions.keep(s, player, _told(), now=NOW) == sessions.KEPT
        await s.commit()
    async with factory() as s:
        player = await s.get(Player, pid)
        assert player.time_zone == "Europe/Moscow"
        longer = _told(ended_at=_unix(NOW), play_seconds=4000, plays=15)
        assert await sessions.keep(s, player, longer, now=NOW) == sessions.KEPT
        shorter = _told(ended_at=_unix(NOW - timedelta(minutes=30)), play_seconds=10, plays=1)
        assert await sessions.keep(s, player, shorter, now=NOW) == sessions.KEPT
        await s.commit()
    async with factory() as s:
        rows = (await s.execute(select(WitnessSession).where(WitnessSession.player_id == pid))).scalars().all()
    assert len(rows) == 1
    assert (rows[0].play_seconds, rows[0].plays, rows[0].ended_at) == (4000, 15, NOW)

async def test_a_session_that_cannot_be_believed_does_not_leave_its_zone_behind(factory):
    naum, _, _ = await _seed(factory)
    pid = await _player(factory, naum)
    async with factory() as s:
        player = await s.get(Player, pid)
        assert await sessions.keep(s, player, _told(play_seconds=-5), now=NOW) == sessions.BAD
        assert player.time_zone is None

async def test_a_zone_alone_is_enough_and_nonsense_is_not(factory):
    naum, _, _ = await _seed(factory)
    pid = await _player(factory, naum)
    async with factory() as s:
        player = await s.get(Player, pid)
        assert await sessions.keep(s, player, {"time_zone": "Asia/Tokyo"}, now=NOW) == sessions.KEPT
        assert player.time_zone == "Asia/Tokyo"
        assert await sessions.keep(s, player, {"time_zone": "Nowhere/Land"}, now=NOW) == sessions.BAD
        assert await sessions.keep(s, player, {}, now=NOW) == sessions.BAD
        assert player.time_zone == "Asia/Tokyo"
        assert await sessions.keep(s, player, _told(time_zone="Nowhere/Land"), now=NOW) == sessions.KEPT
        assert player.time_zone == "Asia/Tokyo"

async def test_the_app_tells_a_session_and_the_titles_can_read_it(served, factory, monkeypatch):
    from utils.title_history import load_history

    monkeypatch.setattr(sessions, "utcnow", lambda: NOW)

    naum, _, _ = await _seed(factory)
    pid = await _player(factory, naum)
    client, mine = served
    first = await client.post("/render/me/session", headers=mine, json=_told())
    assert first.status == 200 and (await first.json())["kept"] is True
    again = await client.post("/render/me/session", headers=mine, json=_told(ended_at=_unix(NOW), play_seconds=4000))
    assert again.status == 200
    assert (await client.post("/render/me/session", headers=mine, json={"plays": 3})).status == 400
    assert (await client.post("/render/me/session", headers={"X-Render-Worker": "mac"}, json=_told())).status in (401, 403, 404)
    async with factory() as s:
        history = await load_history(s, pid)
    assert len(history.spans) == 1 and history.spans[0].play_seconds == 4000
    assert str(history.zone) == "Europe/Moscow"
    assert history.day(NOW.replace(hour=22)) == (NOW + timedelta(days=1)).date()
