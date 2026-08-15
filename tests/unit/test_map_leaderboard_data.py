"""What the map leaderboard card is told: the titles, the average, and the
record changing hands.

The first two are pure and tested as such; the history is walked out of the
attempts table, so it gets an in-memory database in the style of
test_leaderboard_snapshots.py.
"""

from datetime import datetime

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
import db.models  # noqa: F401 — register every table
from db.models.user import User
from db.models.map_attempt import UserMapAttempt
from services.leaderboard.service import (
    _map_average, _map_record_history, _map_titles,
)

CHAT = -1001
MAP = 4242


def row(name, *, pp=0.0, acc=0.0, combo=0, score=0, mods=""):
    return {"username": name, "pp": pp, "accuracy": acc, "combo": combo,
            "score": score, "mods": mods}


# ── the titles ────────────────────────────────────────────────────────────

def test_each_title_names_whoever_actually_holds_it():
    """The point of the panel: four different people can hold four of them, and
    a board that credited the top row with all four would be lying about three."""
    rows = [
        row("Top", pp=400, acc=97.0, combo=1200, score=900_000),
        row("Sharp", pp=380, acc=99.5, combo=1100, score=850_000),
        row("Long", pp=370, acc=96.0, combo=1900, score=800_000),
        row("Heavy", pp=360, acc=95.0, combo=900, score=1_400_000),
    ]
    by_kind = {t["kind"]: t for t in _map_titles(rows, rank_by_score=False)}
    assert by_kind["best"]["who"] == "Top"
    assert by_kind["accuracy"]["who"] == "Sharp"
    assert by_kind["combo"]["who"] == "Long"
    assert by_kind["score"]["who"] == "Heavy"


def test_the_best_result_is_stated_in_the_currency_the_board_ranks_by():
    """A loved map awards no pp, so a board ranked by score must not report a
    "best result" of 0 PP — the number would be true and the claim absurd."""
    rows = [row("A", pp=0.0, score=1_200_000)]
    assert "PP" in _map_titles(rows, rank_by_score=False)[0]["value"]
    assert _map_titles(rows, rank_by_score=True)[0]["value"] == "1,200,000"


def test_the_hardest_mods_win_over_a_bigger_score():
    """The title is about the mods, not the result: whoever brought the heaviest
    combination holds it even if they are last on the board."""
    rows = [row("Plain", pp=500, mods=""), row("Brave", pp=100, mods="HDHR")]
    mods = [t for t in _map_titles(rows, rank_by_score=False) if t["kind"] == "mods"]
    assert mods and mods[0]["who"] == "Brave" and mods[0]["value"] == "HDHR"


def test_an_all_nomod_board_has_no_hardest_mods_line():
    """Rather than a row reading "NM", which says nothing and takes the space
    of something that would."""
    rows = [row("A", pp=300), row("B", pp=200)]
    assert not [t for t in _map_titles(rows, rank_by_score=False) if t["kind"] == "mods"]


def test_an_empty_board_says_nothing():
    assert _map_titles([], rank_by_score=False) == []
    assert _map_average([], rank_by_score=False) == "—"


def test_the_average_follows_the_same_currency():
    rows = [row("A", pp=100, score=1000), row("B", pp=200, score=3000)]
    assert _map_average(rows, rank_by_score=False) == "150.0 PP"
    assert _map_average(rows, rank_by_score=True) == "2,000"


# ── the record history ────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def _seed(session, plays):
    """plays: (username, pp, day) — one attempt each, played that day."""
    users = {}
    for i, (name, _pp, _day) in enumerate(plays, 1):
        if name not in users:
            user = User(chat_id=CHAT, telegram_id=1000 + i, osu_user_id=2000 + i,
                        osu_username=name)
            session.add(user)
            users[name] = user
    await session.flush()
    for i, (name, pp, day) in enumerate(plays, 1):
        session.add(UserMapAttempt(
            user_id=users[name].id, score_id=90000 + i, beatmap_id=MAP,
            pp=pp, score=int(pp * 1000), played_at=datetime(2026, 8, day, 12, 0),
        ))
    await session.commit()


async def test_only_the_plays_that_took_the_record_are_kept(factory):
    """A map with two hundred attempts has a handful of record changes. Every
    play that failed to beat the standing best is not history, it is noise."""
    async with factory() as session:
        await _seed(session, [
            ("Kirill", 300.0, 10),
            ("Den", 250.0, 11),      # worse than the standing record
            ("Peppy", 380.0, 12),
            ("Misha", 370.0, 13),    # also worse
            ("Naum", 420.0, 14),
        ])
        history = await _map_record_history(session, MAP, CHAT, rank_by_score=False)

    assert [h["username"] for h in history] == ["Naum", "Peppy", "Kirill"]
    assert [h["date"] for h in history] == ["14.08", "12.08", "10.08"]


async def test_the_current_holder_comes_first(factory):
    """Newest first: "who holds it now" is the question the strip answers, and
    at the far end of an oldest-first row it is the last thing read."""
    async with factory() as session:
        await _seed(session, [("A", 100.0, 1), ("B", 200.0, 2), ("C", 300.0, 3)])
        history = await _map_record_history(session, MAP, CHAT, rank_by_score=False)
    assert history[0]["username"] == "C"


async def test_a_loved_map_tracks_the_record_by_score(factory):
    """Where pp is always zero, the running maximum has to be the score or the
    history is a single entry that never changes."""
    async with factory() as session:
        users = User(chat_id=CHAT, telegram_id=1, osu_user_id=2, osu_username="A")
        session.add(users)
        await session.flush()
        for i, score in enumerate([100, 500, 300], 1):
            session.add(UserMapAttempt(
                user_id=users.id, score_id=i, beatmap_id=MAP, pp=0.0, score=score,
                played_at=datetime(2026, 8, i, 12, 0),
            ))
        await session.commit()
        history = await _map_record_history(session, MAP, CHAT, rank_by_score=True)
    assert [h["score"] for h in history] == [500, 100]


async def test_a_map_nobody_has_played_has_no_history(factory):
    async with factory() as session:
        assert await _map_record_history(session, MAP, CHAT, rank_by_score=False) == []
