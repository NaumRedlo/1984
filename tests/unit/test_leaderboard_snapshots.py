"""Weekly anchor capture (services/leaderboard/snapshots.py) + the delta board.

In-memory aiosqlite + real ORM, mirroring test_multitenant.py's style.
"""

from datetime import datetime

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
import db.models  # noqa: F401 — register every table
from db.models.user import User
from db.models.leaderboard_snapshot import LeaderboardSnapshot
from services.leaderboard.snapshots import ensure_tenant_snapshot
from services.leaderboard.service import build_delta_board

CHAT = -1001

# Mid-week instants inside two consecutive periods.
W30 = datetime(2026, 7, 22, 12, 0)
W31 = datetime(2026, 7, 29, 12, 0)


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _user(tg, name, **kw):
    base = dict(chat_id=CHAT, telegram_id=tg, osu_username=name, osu_user_id=tg,
                player_pp=1000, accuracy=97.0, play_count=1000, play_time=3600,
                ranked_score=1_000_000, total_hits=400_000, rank="Candidate")
    base.update(kw)
    return User(**base)


async def test_capture_is_idempotent(factory):
    async with factory() as s:
        s.add_all([_user(1, "a"), _user(2, "b")])
        await s.commit()

        assert await ensure_tenant_snapshot(s, CHAT, now=W30) == 2
        await s.commit()
        # Second call in the same period must not duplicate anything.
        assert await ensure_tenant_snapshot(s, CHAT, now=W30) == 0
        await s.commit()

        rows = (await s.execute(select(LeaderboardSnapshot))).scalars().all()
        assert len(rows) == 2
        assert {r.period_key for r in rows} == {"2026-W30"}
        assert rows[0].player_pp == 1000      # froze the value at period start


async def test_a_new_week_opens_a_new_anchor(factory):
    async with factory() as s:
        s.add(_user(1, "a"))
        await s.commit()
        await ensure_tenant_snapshot(s, CHAT, now=W30)
        await s.commit()
        assert await ensure_tenant_snapshot(s, CHAT, now=W31) == 1
        await s.commit()
        keys = {r.period_key for r in (await s.execute(select(LeaderboardSnapshot))).scalars()}
        assert keys == {"2026-W30", "2026-W31"}


async def test_player_who_joined_midweek_gets_an_anchor(factory):
    async with factory() as s:
        s.add(_user(1, "a"))
        await s.commit()
        await ensure_tenant_snapshot(s, CHAT, now=W30)
        await s.commit()

        s.add(_user(2, "late"))
        await s.commit()
        assert await ensure_tenant_snapshot(s, CHAT, now=W30) == 1
        await s.commit()
        assert len((await s.execute(select(LeaderboardSnapshot))).scalars().all()) == 2


async def test_delta_board_reports_collecting_before_any_anchor(factory):
    async with factory() as s:
        s.add(_user(1, "a"))
        await s.commit()
        board = await build_delta_board(s, "pp", CHAT)
        assert board["collecting"] is True and board["rows"] == []


async def test_delta_board_ranks_growth_and_pins_the_viewer(factory):
    async with factory() as s:
        winner, viewer = _user(1, "winner"), _user(2, "viewer")
        s.add_all([winner, viewer])
        await s.commit()
        await ensure_tenant_snapshot(s, CHAT, now=W30)
        await s.commit()

        # Both gain, the winner more.
        winner.player_pp = 1500
        viewer.player_pp = 1100
        await s.commit()

        board = await build_delta_board(s, "pp", CHAT, viewer_user_id=viewer.id)
        assert [r["username"] for r in board["rows"]] == ["winner", "viewer"]
        assert board["rows"][0]["delta"] == 500
        assert board["self_row"]["username"] == "viewer"
        # Viewer sits second; the gap to first is 400 pp of growth.
        assert board["self_row"]["gap_to_next"] == 400
        assert board["no_gain"] == 0


async def test_players_without_growth_are_counted_not_ranked(factory):
    async with factory() as s:
        mover, idle = _user(1, "mover"), _user(2, "idle")
        s.add_all([mover, idle])
        await s.commit()
        await ensure_tenant_snapshot(s, CHAT, now=W30)
        await s.commit()
        mover.player_pp = 1200
        mover.play_count = 1100
        await s.commit()

        board = await build_delta_board(s, "pp", CHAT, viewer_user_id=idle.id)
        assert [r["username"] for r in board["rows"]] == ["mover"]
        assert board["no_gain"] == 1
        # A viewer who hasn't played at all gets a nudge, not a "+0" row.
        assert board["self_not_played"] is True
        assert board["self_row"] is None


async def test_pagination_and_self_row_found_across_pages(factory):
    """Paging must not hide you from yourself: the pinned row is looked up in
    the FULL standings, not just the page being rendered."""
    async with factory() as s:
        players = [_user(i, f"p{i:02d}") for i in range(1, 13)]   # 12 -> 2 pages
        s.add_all(players)
        await s.commit()
        await ensure_tenant_snapshot(s, CHAT, now=W30)
        await s.commit()
        # Descending gains, so p01 leads and p12 trails.
        for n, u in enumerate(players):
            u.player_pp = 1000 + (12 - n) * 10
            u.play_count = 1100
        await s.commit()
        last = players[-1]

        first = await build_delta_board(s, "pp", CHAT, 0, viewer_user_id=last.id)
        assert first["total_pages"] == 2
        assert [r["position"] for r in first["rows"]] == list(range(1, 9))
        # The viewer is 12th — off this page, but still pinned.
        assert first["self_row"]["username"] == "p12"
        assert first["self_row"]["position"] == 12

        second = await build_delta_board(s, "pp", CHAT, 1, viewer_user_id=last.id)
        assert [r["position"] for r in second["rows"]] == [9, 10, 11, 12]
        assert second["page"] == 1

        # Out-of-range pages clamp rather than render an empty card.
        clamped = await build_delta_board(s, "pp", CHAT, 99)
        assert clamped["page"] == 1


async def test_viewer_who_played_but_gained_nothing_still_gets_a_row(factory):
    """"Didn't play" and "played, gained nothing" are different states: the
    second one has something to report, so it keeps its pinned row."""
    async with factory() as s:
        mover, tryer = _user(1, "mover"), _user(2, "tryer")
        s.add_all([mover, tryer])
        await s.commit()
        await ensure_tenant_snapshot(s, CHAT, now=W30)
        await s.commit()
        mover.player_pp = 1200
        tryer.play_count = 1050        # played, but no pp to show for it
        await s.commit()

        board = await build_delta_board(s, "pp", CHAT, viewer_user_id=tryer.id)
        assert not board.get("self_not_played")
        assert board["self_row"]["username"] == "tryer"
        assert board["self_row"]["position"] is None
        # Their lifetime figure is real, not zeroed out.
        assert board["self_row"]["absolute"] == 1000
