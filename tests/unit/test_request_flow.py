"""Request progress derivation and auto-completion on sync.

In-memory aiosqlite + real ORM (mirrors test_render_skin_ownership.py)."""

import contextlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db.database import Base
import db.models  # noqa: F401 — register all tables
from db.models.user import User
from db.models.map_attempt import UserMapAttempt
from db.models.map_request import MapRequest, STATUS_ACCEPTED, STATUS_COMPLETED
from services.requests.conditions import serialize
from services.requests.progress import request_progress
from services.requests import evaluation


@pytest_asyncio.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def _user(**kw):
    base = dict(chat_id=-100, telegram_id=1, osu_username="P", osu_user_id=1)
    base.update(kw)
    return User(**base)


def _attempt(uid, beatmap_id, *, passed, played_at, sid, total=1000, c300=0, miss=0):
    return UserMapAttempt(
        user_id=uid, score_id=sid, beatmap_id=beatmap_id, pp=0.0,
        passed=passed, played_at=played_at, total_objects=total,
        count_300=c300, count_100=0, count_50=0, count_miss=miss,
        accuracy=0.99, max_combo=500, rank="S", is_fc=(miss == 0), mods="HD",
    )


async def test_request_progress_summarizes_attempts(factory):
    async with factory() as s:
        target = _user(telegram_id=2, osu_username="T", osu_user_id=2)
        sender = _user(telegram_id=3, osu_username="S", osu_user_id=3)
        s.add_all([target, sender])
        await s.commit()
        t0 = datetime.now(timezone.utc) - timedelta(hours=1)
        # two fails at ~40-45% (bucket 25-50), one fail at 80% (bucket 75-100)
        s.add_all([
            _attempt(target.id, 555, passed=False, played_at=t0 + timedelta(minutes=1), sid=1, c300=400),
            _attempt(target.id, 555, passed=False, played_at=t0 + timedelta(minutes=2), sid=2, c300=450),
            _attempt(target.id, 555, passed=False, played_at=t0 + timedelta(minutes=3), sid=3, c300=800),
        ])
        req = MapRequest(tenant_chat_id=-100, sender_user_id=sender.id,
                         target_user_id=target.id, beatmap_id=555,
                         conditions=serialize({"pass": True}),
                         status=STATUS_ACCEPTED, responded_at=t0)
        s.add(req)
        await s.commit()

        prog = await request_progress(req, s)
        assert prog["attempt_count"] == 3
        assert prog["max_completion_pct"] == 80.0        # rounded to 0.1
        assert prog["passed"] is False
        assert prog["modal_fail_bucket"] == "25-50"      # two fails cluster there


async def test_evaluate_marks_completed_on_satisfying_pass(factory, monkeypatch):
    monkeypatch.setattr(evaluation, "get_db_session",
                        lambda: _session_cm(factory))
    async with factory() as s:
        target = _user(telegram_id=2, osu_username="T", osu_user_id=2)
        sender = _user(telegram_id=3, osu_username="S", osu_user_id=3)
        s.add_all([target, sender])
        await s.commit()
        t0 = datetime.now(timezone.utc) - timedelta(hours=1)
        # a passing attempt on the requested map, after acceptance
        s.add(_attempt(target.id, 777, passed=True, played_at=t0 + timedelta(minutes=5), sid=99))
        req = MapRequest(tenant_chat_id=-100, sender_user_id=sender.id,
                         target_user_id=target.id, beatmap_id=777,
                         conditions=serialize({"pass": True}),
                         status=STATUS_ACCEPTED, responded_at=t0)
        s.add(req)
        await s.commit()
        req_id = req.id

        # api_client that returns no new recent scores (attempt already stored)
        api = SimpleNamespace(
            get_user_recent_scores=_aret([]),
            sync_user_map_attempts=_aret(0),
        )
        completed = await evaluation.evaluate_open_requests(target, s, api)
        assert [r.id for r in completed] == [req_id]

    async with factory() as s2:
        fresh = await s2.get(MapRequest, req_id)
        assert fresh.status == STATUS_COMPLETED
        assert fresh.completing_score_id == 99
        assert fresh.completed_at is not None


@contextlib.asynccontextmanager
async def _session_cm(factory):
    async with factory() as s:
        yield s


def _aret(value):
    async def _f(*a, **k):
        return value
    return _f
