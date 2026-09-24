import json
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import db.models
from bot.handlers.profile.top_play import split_place
from bot.handlers.profile.update import _row
from db.database import Base
from db.models.track_snapshot import TrackSnapshot
from db.models.user import User
from db.models.user_mode_stats import UserModeStats
from services import tracking
from services.leaderboard import modes


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.mark.parametrize("line, place, rest", [
    ("", None, ""), ("3", 3, ""), ("\\3", 3, ""), ("#12 cookiezi", 12, "cookiezi"),
    ("cookiezi 5", 5, "cookiezi"), ("cookiezi", None, "cookiezi"), ("2019nick", None, "2019nick"),
])
def test_the_place_is_read_from_either_end(line, place, rest):
    assert split_place(line) == (place, rest)


def _user_data(pp, rank, acc=98.0, plays=100):
    return {"pp": pp, "global_rank": rank, "country_rank": rank // 10, "accuracy": acc, "play_count": plays}


def _best(*ids_pp):
    return [{"id": i, "pp": pp} for i, pp in ids_pp]


def test_the_first_update_only_starts_tracking():
    now = tracking.Standing.of(_user_data(8000, 15000), _best((1, 400)))
    changes = tracking.compare(None, now, _best((1, 400)))
    assert changes.first and not changes.nothing and changes.new_scores == []


def test_changes_are_counted_and_new_top_plays_found_with_their_places():
    best_before = _best((1, 400), (2, 380), (3, 300))
    before = tracking.Standing.of(_user_data(8000, 15000, 98.40, 100), best_before)
    best_now = _best((1, 400), (9, 390), (2, 380), (8, 301))
    now = tracking.Standing.of(_user_data(8036.5, 14880, 98.42, 130), best_now)
    changes = tracking.compare(before, now, best_now)
    assert changes.pp == pytest.approx(36.5)
    assert changes.global_rank == 120 and changes.country_rank == 12
    assert changes.accuracy == pytest.approx(0.02) and changes.play_count == 30
    assert [(pos, s["id"]) for pos, s in changes.new_scores] == [(2, 9), (4, 8)]


def test_an_unchanged_player_has_nothing_to_show():
    best = _best((1, 400))
    same = tracking.Standing.of(_user_data(8000, 15000), best)
    assert tracking.compare(same, same, best).nothing


async def test_a_snapshot_is_kept_per_account_and_mode(factory):
    async with factory() as session:
        now = tracking.Standing.of(_user_data(5000, 3000), _best((5, 250), (6, 240)))
        await tracking.save(session, None, 42, 1, now)
        await session.commit()
        kept = await tracking.load(session, 42, 1)
        assert await tracking.load(session, 42, 0) is None
        assert tracking.standing_of(kept).top == [(5, 250.0), (6, 240.0)]
        await tracking.save(session, kept, 42, 1, tracking.Standing.of(_user_data(5100, 2900), _best((7, 260))))
        await session.commit()
        rows = (await session.execute(select(TrackSnapshot))).scalars().all()
    assert len(rows) == 1 and json.loads(rows[0].top) == [[7, 260.0]] and rows[0].pp == 5100


def test_a_new_play_row_says_where_it_landed_and_on_which_client():
    raw = {"id": 1, "pp": 412.3, "accuracy": 0.9912, "max_combo": 900, "rank": "S",
           "mods": [{"acronym": "HD"}, {"acronym": "CL"}], "legacy_score_id": 7,
           "beatmap": {"id": 2, "version": "Extra", "difficulty_rating": 6.4},
           "beatmapset": {"id": 3, "title": "T", "artist": "A"}}
    row = _row(4, raw)
    assert row["client"] == "#4 · stable" and row["mods"] == ["HD"]
    assert row["accuracy"] == pytest.approx(99.12) and row["beatmapset_id"] == 3


class _Osu:
    def __init__(self):
        self.asked = []

    async def get_user_data(self, osu_id, mode=None):
        self.asked.append((osu_id, mode))
        return {"pp": {1: 5000, 2: 0, 3: 6000}.get(osu_id, 0), "global_rank": osu_id * 100,
                "country_rank": osu_id, "accuracy": 97.0, "play_count": 10}


async def _players(factory):
    async with factory() as session:
        for i, (name, pp, rank) in enumerate([("A", 8000, 1500), ("B", 9000, 900), ("C", 0, None)]):
            session.add(User(chat_id=-1, telegram_id=10 + i, osu_user_id=i + 1, osu_username=name,
                             player_pp=pp, global_rank=rank))
        session.add(User(chat_id=-2, telegram_id=99, osu_user_id=9, osu_username="elsewhere", player_pp=99999))
        await session.commit()


async def test_osu_standings_come_from_the_players_own_rows(factory):
    await _players(factory)
    async with factory() as session:
        board = await modes.standings(session, -1, 0)
    assert [(e["user"].osu_username, e["position"]) for e in board] == [("B", 1), ("A", 2)]


async def test_other_modes_are_fetched_once_until_stale_and_nobody_without_pp_is_listed(factory):
    await _players(factory)
    osu = _Osu()
    async with factory() as session:
        assert await modes.refresh(session, osu, -1, 3) == 3
        await session.commit()
        assert await modes.refresh(session, osu, -1, 3) == 0
        board = await modes.standings(session, -1, 3)
        assert [e["user"].osu_username for e in board] == ["C", "A"]
        assert {mode for _, mode in osu.asked} == {"mania"} and len(osu.asked) == 3

        row = (await session.execute(select(UserModeStats).where(UserModeStats.osu_user_id == 1))).scalar_one()
        row.updated_at = datetime.now(timezone.utc) - timedelta(hours=7)
        await session.commit()
        assert await modes.refresh(session, osu, -1, 3) == 1


async def test_a_board_page_marks_the_viewer(factory):
    await _players(factory)
    async with factory() as session:
        entries = await modes.standings(session, -1, 0)
    viewer = entries[1]["user"].id
    board = modes.board(entries, 0, viewer)
    assert board["participants"] == 2 and board["self_row"]["username"] == "A"
    assert board["rows"][0]["value_label"] == "#900" and board["rows"][0]["sub_label"] == "9 000pp"


class _Member:
    def __init__(self, status, name):
        self.status = status
        self.user = type("U", (), {"full_name": name, "username": None})()


class _Bot:
    def __init__(self, members):
        self.members = members

    async def get_chat_member(self, chat_id, telegram_id):
        return self.members[(chat_id, telegram_id)]


class _Message:
    def __init__(self, bot):
        self.bot, self.said = bot, []
        self.from_user = type("F", (), {"id": 500})()

    async def answer(self, text, **_):
        self.said.append(text)


async def test_find_names_only_this_chats_members_who_linked_the_account(factory, monkeypatch):
    import contextlib

    from bot.filters import TriggerArgs
    from bot.handlers.profile import find

    async with factory() as session:
        session.add_all([
            User(chat_id=-1, telegram_id=10, osu_user_id=7, osu_username="OldName"),
            User(chat_id=-4, telegram_id=11, osu_user_id=7, osu_username="OldName"),
            User(chat_id=-2, telegram_id=12, osu_user_id=7, osu_username="OldName"),
        ])
        await session.commit()

    @contextlib.asynccontextmanager
    async def session_of():
        async with factory() as s:
            yield s

    async def resolved(_client, query):
        return {"id": 7, "username": "NewName"}

    async def english(_tg):
        return "en"

    async def allowed(*_a):
        return True

    monkeypatch.setattr(find, "get_db_session", session_of)
    monkeypatch.setattr(find, "resolve_osu_user", resolved)
    monkeypatch.setattr(find, "get_language", english)
    monkeypatch.setattr(find, "ensure_dm_tenant", allowed)
    bot = _Bot({(-1, 10): _Member("member", "Alice <3"), (-4, 11): _Member("left", "Bob")})
    message = _Message(bot)
    await find.cmd_find(message, TriggerArgs("find", "oldname", "find oldname"), osu_api_client=object(),
                        tenant_chat_id=-1)
    assert message.said == ['<b>NewName</b> in this chat: <a href="tg://user?id=10">Alice &lt;3</a>']

    for chat in (-3, -4):  # nobody linked it there; the one who did has left
        message = _Message(bot)
        await find.cmd_find(message, TriggerArgs("find", "x", "find x"), osu_api_client=object(), tenant_chat_id=chat)
        assert "Nobody in this chat" in message.said[0]
