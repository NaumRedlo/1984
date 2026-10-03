from datetime import timedelta, timezone

import pytest

from db.models.map_attempt import UserMapAttempt
from db.models.witnessed_play import WitnessedPlay
from services.render_farm import community, witnessed
from tests.unit.test_render_farm_community import CHAT, NOW, _seed, factory, served

MD5 = "0123456789abcdef0123456789abcdef"
REPLAY = "fedcba9876543210fedcba9876543210"

def _told(**kw):
    base = dict(md5=MD5, replay=REPLAY, id=555, set=77, artist="xi", title="Blue Zenith", version="FOUR DIMENSIONS",
                creator="Asphyxia", mods=8 | 16, score=1_234_567, max_combo=640, n300=500, n100=12, n50=1, geki=90,
                katu=8, miss=0, passed=True)
    base.update(kw)
    return base

class _Osu:
    def __init__(self, checksum=MD5):
        self.checksum, self.asked = checksum, 0

    async def get_beatmap(self, beatmap_id):
        self.asked += 1
        return {"id": beatmap_id, "checksum": self.checksum, "version": "FOUR DIMENSIONS", "difficulty_rating": 7.07,
                "bpm": 200, "total_length": 250, "max_combo": 650, "status": "ranked",
                "beatmapset": {"id": 77, "artist": "xi", "title": "Blue Zenith", "creator": "Asphyxia"}}

def test_mods_are_said_the_way_the_rest_of_the_bot_says_them():
    assert witnessed.mods_said(0) == []
    assert witnessed.mods_said(8 | 16) == ["HD", "HR"]
    assert witnessed.mods_said(64 | 512 | 8) == ["HD", "NC"]
    assert witnessed.mods_said(32 | 16384) == ["PF"]

def test_a_grade_is_counted_the_way_the_client_counts_it():
    assert witnessed.grade_of(100, 0, 0, 0, 0, True) == "X"
    assert witnessed.grade_of(100, 0, 0, 0, 8, True) == "XH"
    assert witnessed.grade_of(95, 5, 0, 0, 0, True) == "S"
    assert witnessed.grade_of(95, 5, 0, 0, 1024, True) == "SH"
    assert witnessed.grade_of(95, 4, 0, 1, 0, True) == "A"
    assert witnessed.grade_of(85, 15, 0, 0, 0, True) == "A"
    assert witnessed.grade_of(75, 25, 0, 0, 0, True) == "B"
    assert witnessed.grade_of(65, 30, 0, 5, 0, True) == "C"
    assert witnessed.grade_of(50, 30, 0, 20, 0, True) == "D"
    assert witnessed.grade_of(100, 0, 0, 0, 0, False) == "F"
    assert witnessed.accuracy_of(84, 14, 1, 0) == pytest.approx(89.73)

def test_what_is_not_a_play_is_refused():
    assert witnessed.read(_told(), NOW) is not None
    assert witnessed.read(_told(md5="nonsense"), NOW) is None
    assert witnessed.read(_told(replay=""), NOW) is None
    assert witnessed.read(_told(n300=-1), NOW) is None
    assert witnessed.read(_told(n300=True), NOW) is None
    assert witnessed.read(_told(score="many"), NOW) is None
    assert witnessed.read(_told(n300=0, n100=0, n50=0, miss=0), NOW) is None
    assert witnessed.read(_told(max_combo=10 ** 9), NOW) is None
    assert witnessed.read(_told(mods=8 | 2048), NOW) is None
    assert witnessed.read(_told(mods=4194304), NOW) is None

def test_the_time_of_a_play_survives_a_delivery_backlog():
    near = int((NOW - timedelta(minutes=2)).replace(tzinfo=timezone.utc).timestamp())
    assert witnessed.read(_told(ended=near), NOW)["played"] == NOW - timedelta(minutes=2)
    assert witnessed.read(_told(ended=near - 86_400 * 3), NOW)["played"] == NOW - timedelta(days=3, minutes=2)
    assert witnessed.read(_told(ended=near - 86_400 * 15), NOW) is None
    assert witnessed.read(_told(ended=near + 86_400), NOW)["played"] == NOW
    assert witnessed.read(_told(ended="soon"), NOW)["played"] == NOW

async def _player(factory, user_id):
    from db.models.user import User

    async with factory() as s:
        return (await s.get(User, user_id)).player_id

async def test_a_play_is_kept_once_and_its_map_learnt_afterwards(factory):
    naum, _, _ = await _seed(factory)
    player = await _player(factory, naum)
    async with factory() as s:
        verdict, row = await witnessed.keep(s, player, _told(), now=NOW)
        assert verdict == witnessed.KEPT and row.rank == "SH" and row.mods == "HD,HR"
        assert row.accuracy == pytest.approx(98.28, abs=0.01) and row.star_rating is None
        again, same = await witnessed.keep(s, player, _told(), now=NOW + timedelta(minutes=1))
        assert again == witnessed.SEEN and same.id == row.id
        play = row.id
    osu = _Osu()
    async with factory() as s:
        assert await witnessed.learn(s, osu, play) is True
    async with factory() as s:
        row = await s.get(WitnessedPlay, play)
        assert (row.star_rating, row.bpm, row.length, row.map_max_combo, row.status) == (7.07, 200.0, 250, 650, "ranked")

def test_what_the_client_knows_of_a_map_is_believed_only_when_it_could_be_true():
    told = witnessed.facts_told({"stars": 6.254, "bpm": 180.0, "length": 120, "status": "ranked", "ar": 9})
    assert told == {"stars": 6.25, "bpm": 180.0, "length": 120, "status": "ranked"}
    assert witnessed.facts_told({"stars": 0, "bpm": -3, "length": 10 ** 9, "status": "frozen"}) is None
    assert witnessed.facts_told({"stars": True, "bpm": "fast"}) is None
    assert witnessed.facts_told(None) is None and witnessed.facts_told([1]) is None
    assert witnessed.facts_told({"stars": 5.0, "status": "frozen"}) == {"stars": 5.0, "bpm": None, "length": None, "status": None}
    assert witnessed.read(_told(facts={"stars": 7.07, "length": 250}), NOW)["facts"] == {"stars": 7.07, "bpm": None, "length": 250, "status": None}

async def test_a_play_told_with_the_clients_own_numbers_has_them_until_osu_says_better(factory):
    naum, _, _ = await _seed(factory)
    player = await _player(factory, naum)
    async with factory() as s:
        _, row = await witnessed.keep(s, player, _told(id=0, facts={"stars": 4.2, "bpm": 150.0, "length": 99, "status": "unsubmitted"}), now=NOW)
        play = row.id
        assert (row.star_rating, row.bpm, row.length, row.status) == (4.2, 150.0, 99, "unsubmitted")
    async with factory() as s:
        assert await witnessed.learn(s, _Osu(), play) is False, "a map osu! does not know teaches nothing"
        assert (await s.get(WitnessedPlay, play)).star_rating == 4.2
    async with factory() as s:
        _, other = await witnessed.keep(s, player, _told(replay="c" * 32, facts={"stars": 4.2, "bpm": 150.0, "length": 99, "status": "ranked"}), now=NOW + timedelta(minutes=1))
        other_id = other.id
    async with factory() as s:
        assert await witnessed.learn(s, _Osu(), other_id) is True
        row = await s.get(WitnessedPlay, other_id)
        assert (row.star_rating, row.bpm, row.length) == (7.07, 200.0, 250), "what osu! says replaces what the client said"

async def test_a_map_that_is_not_the_one_named_teaches_nothing(factory):
    naum, _, _ = await _seed(factory)
    player = await _player(factory, naum)
    async with factory() as s:
        _, row = await witnessed.keep(s, player, _told(id=99_001), now=NOW)
        play = row.id
    async with factory() as s:
        assert await witnessed.learn(s, _Osu(checksum="f" * 32), play) is False
        assert (await s.get(WitnessedPlay, play)).star_rating is None

async def test_plays_do_not_come_faster_than_they_can_be_played(factory):
    naum, _, _ = await _seed(factory)
    player = await _player(factory, naum)
    async with factory() as s:
        assert (await witnessed.keep(s, player, _told(), now=NOW))[0] == witnessed.KEPT
        assert (await witnessed.keep(s, player, _told(replay="a" * 32), now=NOW + timedelta(seconds=1)))[0] == witnessed.TOO_SOON
        assert (await witnessed.keep(s, player, _told(replay="a" * 32), now=NOW + timedelta(seconds=30)))[0] == witnessed.KEPT

async def test_a_witnessed_play_is_in_the_feed_until_osu_confirms_it(factory):
    naum, koto, _ = await _seed(factory)
    player = await _player(factory, naum)
    async with factory() as s:
        await witnessed.keep(s, player, _told(), now=NOW - timedelta(seconds=20))
    async with factory() as s:
        body = await community.gather(s, CHAT, 7, now=NOW)
    first = body["live"][0]
    assert first["witnessed"] is True and first["who"] == naum and first["id"] is None
    assert first["map"]["title"] == "Blue Zenith" and first["grade"] == "S" and first["mods"] == ["HD", "HR"]
    assert first["counts"] == [500, 12, 1, 0] and first["passed"] is True and first["pp"] == 0.0
    assert [play["id"] for play in body["live"][1:]] == [10, 11]

    async with factory() as s:
        s.add(UserMapAttempt(player_id=player, score_id=900, beatmap_id=555, pp=401.0, accuracy=98.28, rank="SH",
                             mods="HD,HR", artist="xi", title="Blue Zenith", version="FOUR DIMENSIONS", score=1_234_567,
                             max_combo=640, count_100=12, count_50=1, count_miss=0,
                             played_at=NOW - timedelta(seconds=40), passed=True))
        await s.commit()
    async with factory() as s:
        body = await community.gather(s, CHAT, 7, now=NOW)
    assert [play["id"] for play in body["live"]] == [900, 10, 11]
    assert not any(play.get("witnessed") for play in body["live"])

async def test_another_play_of_the_same_map_does_not_hide_a_witnessed_one(factory):
    naum, _, _ = await _seed(factory)
    player = await _player(factory, naum)
    async with factory() as s:
        await witnessed.keep(s, player, _told(), now=NOW - timedelta(seconds=20))
        s.add(UserMapAttempt(player_id=player, score_id=901, beatmap_id=555, pp=0.0, accuracy=80.0, rank="F",
                             mods="HD,HR", score=300_000, max_combo=120, count_100=40, count_50=6, count_miss=11,
                             played_at=NOW - timedelta(minutes=4), passed=False))
        await s.commit()
    async with factory() as s:
        body = await community.gather(s, CHAT, 7, now=NOW)
    assert [play.get("witnessed", False) for play in body["live"][:2]] == [True, False]

async def test_the_app_tells_a_play_and_the_chat_sees_it(served, factory, monkeypatch):
    from services.render_farm import http
    from tasks import live_tracker

    naum, _, _ = await _seed(factory)
    client, mine = served
    osu = _Osu()
    monkeypatch.setattr(http, "_osu", osu)
    tracker = live_tracker.LiveTracker(None)
    live_tracker.set_current(tracker)
    try:
        reply = await client.post("/render/me/play", headers=mine, json=_told(id=5561))
        assert reply.status == 201 and (await reply.json())["kept"] is True
        assert 70 in tracker.nudged
        tracker.nudged.clear()
        failed = await client.post("/render/me/play", headers=mine, json=_told(id=5561, replay="b" * 32, passed=False, ended=None))
        assert failed.status == 429
        again = await client.post("/render/me/play", headers=mine, json=_told(id=5561))
        assert again.status == 200 and (await again.json())["kept"] is False
        assert (await client.post("/render/me/play", headers=mine, json={"md5": "x"})).status == 400
        assert (await client.post("/render/me/play", headers={"X-Render-Worker": "mac"}, json=_told())).status in (401, 403, 404)
    finally:
        live_tracker.set_current(None)
    got = await (await client.get("/render/community", headers=mine)).json()
    first = got["live"][0]
    assert first["witnessed"] is True and first["who"] == naum
    assert first["map"]["stars"] == 7.07 and first["max_combo"] == 650 and osu.asked == 1
    card = await (await client.get(f"/render/community/person?chat={CHAT}&id={naum}", headers=mine)).json()
    assert card["recent"][0]["witnessed"] is True and card["recent"][0]["map"]["beatmap"] == 5561
