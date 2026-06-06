"""_do_accept eligibility gates: min_hp / min_rank / max_participants.

These were advertised on the bounty card but never enforced; this locks in the
accept-time checks. Self-contained temp-SQLite DB driven through the real
``_do_accept`` and ORM models (no pytest-asyncio — asyncio.run() drives it).
"""

import asyncio
import os
import tempfile

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bot.handlers.bounty.handlers import RANK_ORDER, _do_accept
from db.database import Base
from db.models.bounty import Bounty
from db.models.user import User


def _bounty(bounty_id, **kw):
    base = dict(
        bounty_id=bounty_id, title="t", beatmap_id=10, beatmap_title="m",
        star_rating=5.0, drain_time=120, status="active", created_by=1,
    )
    base.update(kw)
    return Bounty(**base)


def _user(tg, hp=0):
    return User(chat_id=-100, telegram_id=tg, osu_username=f"u{tg}",
                osu_user_id=tg, hps_points=hp)


async def _exercise() -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    eng = create_async_engine(f"sqlite+aiosqlite:///{path}")
    Session = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with eng.begin() as c:
            await c.run_sync(Base.metadata.create_all)

        # ── min_hp ───────────────────────────────────────────────────────
        async with Session() as s:
            s.add(_bounty("HP", min_hp=500))
            u = _user(1, hp=100)
            s.add(u)
            await s.commit()
            ok, msg = await _do_accept(s, u, "HP")
            assert ok is False and "HP" in msg, (ok, msg)
            u.hps_points = 600
            await s.commit()
            ok, msg = await _do_accept(s, u, "HP")
            assert ok is True, (ok, msg)

        # ── min_rank (bottom-rank user vs top-rank gate → blocked) ───────
        async with Session() as s:
            s.add(_bounty("RANK", min_rank=RANK_ORDER[-1]))
            u = _user(2, hp=0)
            s.add(u)
            await s.commit()
            ok, msg = await _do_accept(s, u, "RANK")
            assert ok is False and "ранг" in msg.lower(), (ok, msg)

        # ── max_participants (second distinct user rejected) ─────────────
        async with Session() as s:
            s.add(_bounty("MP", max_participants=1))
            a, b = _user(10, hp=0), _user(11, hp=0)
            s.add_all([a, b])
            await s.commit()
            ok, _ = await _do_accept(s, a, "MP")
            assert ok is True
            ok, msg = await _do_accept(s, b, "MP")
            assert ok is False and "Лимит" in msg, (ok, msg)

        # ── no gates → plain accept succeeds ─────────────────────────────
        async with Session() as s:
            s.add(_bounty("OPEN"))
            u = _user(20, hp=0)
            s.add(u)
            await s.commit()
            ok, _ = await _do_accept(s, u, "OPEN")
            assert ok is True
    finally:
        await eng.dispose()
        os.unlink(path)


def test_accept_eligibility_gates():
    asyncio.run(_exercise())
