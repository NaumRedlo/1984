from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
from db.models.local_score import LocalScore
from db.models.player import Player
from db.models.user import User
from services.render_farm import local_scores
from tests.unit.test_render_farm_community import NOW, _seed, factory, served
from utils.title_history import load_history
from utils.title_progress import refresh_user_titles

MD5 = "0123456789abcdef0123456789abcdef"

def _unix(moment):
    return int(moment.replace(tzinfo=timezone.utc).timestamp())

def _row(**kw):
    base = dict(md5=MD5, id=555, set=77, played=_unix(NOW - timedelta(days=3)), score=1_234_567, combo=640, n300=500, n100=12, n50=1,
                geki=90, katu=8, miss=0, perfect=True, mods=8 | 64, stars=7.5, base_stars=5.2, ar=9.0, cs=4.0, od=8.0, hp=6.0, bpm=180.0,
                length=120, objects=300, status="ranked")
    base.update(kw)
    return base

def test_a_local_score_is_believed_only_when_it_could_be_one():
    assert local_scores.read(_row(), NOW) is not None
    kept = local_scores.read(_row(), NOW)
    assert (kept["beatmap_id"], kept["count_300"], kept["perfect"], kept["mods"], kept["star_rating"], kept["status"]) == (555, 500, True, 72, 7.5, "ranked")
    for broken in (_row(md5="nonsense"), _row(id=0), _row(id=True), _row(played=100), _row(played=_unix(NOW + timedelta(days=30))),
                   _row(score=-1), _row(n300=-5), _row(n300=0, n100=0, n50=0, miss=0), _row(mods=2048), _row(mods=4_194_304),
                   _row(combo=10 ** 9), _row(played="yesterday")):
        assert local_scores.read(broken, NOW) is None, broken
    assert local_scores.read(["not", "a", "dict"], NOW) is None
    odd = local_scores.read(_row(stars=0, base_stars=99, status="frozen", ar=True, bpm=-1, length=10 ** 9), NOW)
    assert (odd["star_rating"], odd["base_star_rating"], odd["status"], odd["ar"], odd["bpm"], odd["length"]) == (None, None, None, None, None, None)

async def _player(factory, user_id):
    async with factory() as s:
        return (await s.get(User, user_id)).player_id

async def test_scores_are_kept_once_and_the_count_that_is_new_is_told(factory):
    naum, _, _ = await _seed(factory)
    pid = await _player(factory, naum)
    rows = [_row(played=_unix(NOW) - 1000 + at, score=100_000 + at) for at in range(10)]
    async with factory() as s:
        assert await local_scores.keep(s, pid, {"scores": rows}, now=NOW) == (local_scores.KEPT, 10)
    async with factory() as s:
        assert await local_scores.keep(s, pid, {"scores": rows + [_row(played=_unix(NOW) - 500, score=1)]}, now=NOW) == (local_scores.KEPT, 1)
        assert await local_scores.keep(s, pid, {"scores": rows}, now=NOW) == (local_scores.KEPT, 0)
        assert (await local_scores.keep(s, pid, {"scores": []}, now=NOW))[0] == local_scores.BAD
        assert (await local_scores.keep(s, pid, {"scores": [{"x": 1}]}, now=NOW))[0] == local_scores.BAD
        assert (await local_scores.keep(s, pid, {"scores": [_row()] * (local_scores.ROWS_MOST + 1)}, now=NOW))[0] == local_scores.BAD
        assert (await local_scores.keep(s, pid, "scores", now=NOW))[0] == local_scores.BAD
        total = (await s.execute(select(func.count(LocalScore.id)))).scalar()
    assert total == 11

async def test_what_is_rejected_among_good_rows_does_not_spoil_them(factory):
    naum, _, _ = await _seed(factory)
    pid = await _player(factory, naum)
    async with factory() as s:
        assert await local_scores.keep(s, pid, {"scores": [_row(), _row(md5="bad", score=5), _row(score=7)]}, now=NOW) == (local_scores.KEPT, 2)

async def test_a_player_cannot_keep_more_than_the_most(factory, monkeypatch):
    naum, _, _ = await _seed(factory)
    pid = await _player(factory, naum)
    monkeypatch.setattr(local_scores, "PLAYER_MOST", 3)
    async with factory() as s:
        assert await local_scores.keep(s, pid, {"scores": [_row(score=n + 1) for n in range(3)]}, now=NOW) == (local_scores.KEPT, 3)
        assert await local_scores.keep(s, pid, {"scores": [_row(score=99)]}, now=NOW) == (local_scores.TOO_MANY, 0)

async def test_the_app_tells_its_local_scores_and_the_chat_has_them_once(served, factory, monkeypatch):
    naum, _, _ = await _seed(factory)
    pid = await _player(factory, naum)
    monkeypatch.setattr(local_scores, "utcnow", lambda: NOW)
    client, mine = served
    rows = [_row(score=1000 + at, played=_unix(NOW) - 5000 + at) for at in range(5)]
    first = await client.post("/render/me/history", headers=mine, json={"scores": rows})
    assert first.status == 200 and (await first.json())["kept"] == 5
    again = await client.post("/render/me/history", headers=mine, json={"scores": rows})
    assert again.status == 200 and (await again.json())["kept"] == 0
    assert (await client.post("/render/me/history", headers=mine, json={"scores": []})).status == 400
    assert (await client.post("/render/me/history", headers={"X-Render-Worker": "mac"}, json={"scores": rows})).status in (401, 403, 404)
    async with factory() as s:
        history = await load_history(s, pid)
    assert len(history.local_scores) == 5 and history.local_scores[0].table == "local"

@pytest_asyncio.fixture
async def titles_db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'local.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()

async def _titles(factory, rows, zone=None):
    async with factory() as s:
        user = User(chat_id=-100, telegram_id=1, osu_username="alice", osu_user_id=1001, play_count=0)
        s.add(user)
        await s.commit()
        uid, pid = user.id, user.player_id
    async with factory() as s:
        if zone:
            (await s.get(Player, pid)).time_zone = zone
        for at, row in enumerate(rows):
            s.add(LocalScore(player_id=pid, created_at=NOW, **{**local_scores.read(row, NOW + timedelta(days=400)), "score": row.get("score", 1000 + at)}))
        await s.commit()
    async with factory() as s:
        user = await s.get(User, uid)
        return {p["code"]: p for p in await refresh_user_titles(user, s)}

async def test_a_score_from_before_the_bot_was_there_can_earn_a_title_for_the_map_it_was_played_on(titles_db):
    progress = await _titles(titles_db, [_row(n300=900, n100=0, n50=0, miss=0, mods=64, stars=8.1, base_stars=5.8, bpm=240.0, perfect=True, played=_unix(NOW) - 86400 * 400)])
    assert progress["fc_bpm_210"]["unlocked"], "an FC of a map of 6 stars at 360 BPM with Double Time"
    assert progress["ss_7star"]["unlocked"] is False, "7 stars are counted on the map's own stars for this title, 5.8 is not enough"
    assert progress["ss_8star"]["unlocked"] is False

async def test_stars_with_the_mods_are_what_the_rules_that_ask_for_them_read(titles_db):
    progress = await _titles(titles_db, [_row(n300=900, n100=0, n50=0, miss=0, mods=64 | 8, stars=8.6, base_stars=6.2, bpm=200.0, length=100)])
    assert progress["two_minutes_hate"]["unlocked"] is True
    assert progress["fc_bpm_250"]["unlocked"], "7 stars with the mods at 300 BPM with DT"

async def test_a_local_score_with_a_mod_that_plays_itself_earns_nothing(titles_db):
    progress = await _titles(titles_db, [_row(n300=900, n100=0, n50=0, miss=0, mods=128 | 64, stars=8.6, base_stars=6.2, bpm=240.0)])
    assert not progress["fc_bpm_210"]["unlocked"] and not progress["two_minutes_hate"]["unlocked"]

async def test_coincidences_are_found_among_local_scores_too(titles_db):
    progress = await _titles(titles_db, [_row(id=1, md5="a" * 32, score=777_000, combo=1984), _row(id=2, md5="b" * 32, score=777_000, n300=999, n100=0, n50=1, miss=0, perfect=False, combo=500)])
    assert progress["dejavu"]["unlocked"] and progress["combo_1984"]["unlocked"]

async def test_a_week_of_ss_in_the_players_own_days_can_come_from_the_local_scores(titles_db):
    rows = [_row(id=10 + n, md5=f"{n:032x}", score=2000 + n, played=_unix(datetime(2026, 3, 2 + n, 21, 30)), n300=900, n100=0, n50=0, miss=0, mods=0) for n in range(7)]
    progress = await _titles(titles_db, rows, zone="Asia/Tokyo")
    assert progress["perfect_week"]["unlocked"] and progress["perfect_week"]["current"] == 7
