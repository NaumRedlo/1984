import asyncio
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from sqlalchemy import delete, func, select, update

from db.models.render_job import RenderJob

from utils.logger import get_logger

logger = get_logger("services.render_farm.queue")

LEASE_SECONDS = 90.0
MAX_AGE_SECONDS = 3600.0
MAX_ATTEMPTS = 3
CLOSE_AFTER = 6 * 3600.0

ARRIVED = "render_job_arrived"
CHANGED = "render_job_changed"

STANDARD = {"width": 1920, "height": 1080, "fps": 60, "loudness": -16.0,
            "play": {"map_sounds": False, "storyboard": True, "map_video": True,
                     "pp_counter": True, "strain_graph": True}}

RIVALS = 50

_noting: set[asyncio.Task] = set()


def _picture(url: Any) -> str:
    said = str(url or "").strip()
    return said if said.startswith("https://a.ppy.sh/") else ""


def chat_rivals_of(rows: Any) -> list[dict[str, Any]]:
    found = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        points = row.get("score")
        if not name or not isinstance(points, (int, float)) or points <= 0:
            continue
        accuracy = row.get("accuracy")
        mods = row.get("mods") or []
        found.append({
            "name": name,
            "score": int(points),
            "standard": int(points),
            "accuracy": round(float(accuracy), 2) if isinstance(accuracy, (int, float)) else None,
            "mods": "".join(str(mod) for mod in mods) if isinstance(mods, list) else str(mods),
            "avatar": _picture(row.get("avatar")),
        })
    return found[:RIVALS]


def rivals_of(scores: Any) -> list[dict[str, Any]]:
    found = []
    for score in scores if isinstance(scores, list) else []:
        if not isinstance(score, dict):
            continue
        name = str(((score.get("user") or {}).get("username")) or "").strip()
        points = next((int(score[key]) for key in ("legacy_total_score", "classic_total_score", "total_score")
                       if isinstance(score.get(key), (int, float)) and score[key] > 0), 0)
        if not name or points <= 0:
            continue
        accuracy = score.get("accuracy")
        mods = "".join(str(mod.get("acronym") if isinstance(mod, dict) else mod) for mod in score.get("mods") or []
                       if (mod.get("acronym") if isinstance(mod, dict) else mod) not in ("CL",))
        standard = score.get("total_score")
        found.append({
            "name": name,
            "score": points,
            "stable": next((int(score[key]) for key in ("legacy_total_score", "classic_total_score")
                            if isinstance(score.get(key), (int, float)) and score[key] > 0), None),
            "standard": int(standard) if isinstance(standard, (int, float)) and standard > 0 else None,
            "accuracy": round(float(accuracy) * 100.0, 2) if isinstance(accuracy, (int, float)) else None,
            "mods": mods,
            "avatar": _picture((score.get("user") or {}).get("avatar_url")),
        })
    return found[:RIVALS]


class State(str, Enum):
    WAITING = "waiting"
    CLAIMED = "claimed"
    SETTLED = "settled"
    WITHDRAWN = "withdrawn"

OPEN = (State.WAITING.value, State.CLAIMED.value)
CLOSED = (State.SETTLED.value, State.WITHDRAWN.value)

@dataclass
class Job:
    id: str
    replay_path: str
    title: str
    beatmap_md5: str
    beatmapset_id: Optional[int]
    skin: Optional[dict[str, Any]]
    requester: int
    chat_id: int
    created: float
    state: State = State.WAITING
    worker: Optional[str] = None
    worker_name: str = ""
    lease_until: float = 0.0
    attempts: int = 0
    progress: Optional[dict[str, Any]] = None
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    payload: Optional[dict[str, Any]] = None
    withdrawn: bool = False
    reason: str = ""
    video_id: Optional[int] = None
    settings: Optional[dict[str, Any]] = None
    in_app: bool = False
    video_meta: Optional[dict[str, Any]] = None
    ruleset: int = 0
    leaderboard: list[dict[str, Any]] = field(default_factory=list)
    troubles: list[str] = field(default_factory=list)
    requester_player: Optional[int] = None
    followed: bool = False
    told: Optional[dict[str, Any]] = None

    def belongs_to(self, telegram_id: int, player_id: Optional[int]) -> bool:
        if telegram_id:
            return self.requester == telegram_id
        return player_id is not None and self.requester == 0 and self.requester_player == player_id

    def served_by(self, worker: "Serves") -> bool:
        return self.belongs_to(worker.telegram_id, worker.player_id) or (self.chat_id < 0 and self.chat_id in worker.chats)

    def handed(self) -> dict[str, Any]:
        skin = None
        if self.skin:
            skin = {"name": self.skin["name"], "hash": self.skin["hash"], "size": self.skin.get("size", 0)}
        return {
            "id": self.id,
            "title": self.title,
            "beatmap_md5": self.beatmap_md5,
            "beatmapset_id": self.beatmapset_id,
            "settings": dict(self.settings or STANDARD),
            "skin": skin,
            "lease_seconds": LEASE_SECONDS,
            "leaderboard": list(self.leaderboard),
        }

def _trouble(job: Job, worker: str, reason: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    from services import incidents

    task = loop.create_task(incidents.note("farm", "worker", reason, telegram_id=job.requester or None, chat_id=job.chat_id or None, detail={
        "job": job.id, "title": job.title, "worker": worker, "attempt": job.attempts, "map": job.beatmap_md5,
        "ruleset": job.ruleset, "in_app": job.in_app,
    }))
    _noting.add(task)
    task.add_done_callback(_noting.discard)

@dataclass(frozen=True)
class Serves:
    telegram_id: int
    player_id: Optional[int] = None
    chats: frozenset[int] = frozenset()

def _signals():
    from utils.signals import shared

    return shared

def _leftovers(row: RenderJob) -> None:
    path = (row.payload or {}).get("path")
    if path:
        try:
            os.unlink(path)
        except OSError:
            pass
    workdir = (row.told or {}).get("workdir")
    if workdir:
        shutil.rmtree(workdir, ignore_errors=True)

class RenderQueue:
    def __init__(self) -> None:
        self._known: dict[str, Job] = {}

    def _factory(self):
        from db.database import AsyncSessionFactory

        return AsyncSessionFactory()

    def arrival(self) -> asyncio.Event:
        signals = _signals()
        signals.listen(ARRIVED)
        return signals.mark(ARRIVED)

    def _fresh(self, row: RenderJob) -> Job:
        job = self._known.get(row.id)
        if job is None:
            job = Job(id=row.id, replay_path=row.replay_path, title=row.title, beatmap_md5=row.beatmap_md5,
                      beatmapset_id=row.beatmapset_id, skin=None, requester=row.requester, chat_id=row.chat_id,
                      created=row.created)
            self._known[row.id] = job
        job.skin = dict(row.skin) if row.skin else None
        job.state = State(row.state)
        job.worker = row.worker
        job.worker_name = row.worker_name or ""
        job.lease_until = row.lease_until or 0.0
        job.attempts = row.attempts or 0
        job.progress = row.progress
        job.payload = row.payload
        job.reason = row.reason or ""
        job.video_id = row.video_id
        job.settings = dict(row.settings) if row.settings else None
        job.in_app = bool(row.in_app)
        job.video_meta = dict(row.video_meta) if row.video_meta else None
        job.ruleset = row.ruleset or 0
        job.leaderboard = list(row.leaderboard or [])
        job.troubles = list(row.troubles or [])
        job.requester_player = row.requester_player
        job.followed = bool(row.followed)
        job.told = dict(row.told) if row.told else None
        job.withdrawn = job.state is State.WITHDRAWN
        if row.state in CLOSED:
            job.settled.set()
        return job

    def _ended(self, job_id: str, state: State, *, payload: Optional[dict[str, Any]] = None, reason: Optional[str] = None,
               keep: bool = False) -> None:
        job = self._known.get(job_id) if keep else self._known.pop(job_id, None)
        if job is None:
            return
        job.state = state
        job.withdrawn = state is State.WITHDRAWN
        if payload is not None:
            job.payload = payload
        if reason is not None:
            job.reason = reason
        job.settled.set()

    async def _tell(self, session, channel: str) -> None:
        await _signals().announce(session, channel)

    async def offer(self, replay_path: str, title: str, *, beatmap_md5: str, beatmapset_id: Optional[int] = None,
                    skin: Optional[dict[str, Any]] = None, requester: int = 0, chat_id: int = 0,
                    now: Optional[float] = None, video_id: Optional[int] = None,
                    settings: Optional[dict[str, Any]] = None, in_app: bool = False,
                    video_meta: Optional[dict[str, Any]] = None, ruleset: int = 0,
                    leaderboard: Optional[list[dict[str, Any]]] = None,
                    requester_player: Optional[int] = None, followed: bool = False) -> Job:
        row = RenderJob(
            id=uuid.uuid4().hex[:16],
            replay_path=replay_path,
            title=title,
            beatmap_md5=beatmap_md5,
            beatmapset_id=beatmapset_id,
            skin=dict(skin) if skin else None,
            requester=requester,
            chat_id=chat_id,
            created=now if now is not None else time.time(),
            state=State.WAITING.value,
            worker_name="",
            lease_until=0.0,
            attempts=0,
            reason="",
            video_id=video_id,
            settings=dict(settings) if settings else None,
            in_app=in_app,
            video_meta=dict(video_meta) if video_meta else None,
            ruleset=ruleset,
            leaderboard=list(leaderboard or []),
            troubles=[],
            requester_player=requester_player,
            followed=followed,
        )
        async with self._factory() as session:
            session.add(row)
            await session.flush()
            await self._tell(session, ARRIVED)
            await session.commit()
            job = self._fresh(row)
        logger.info("job %s offered: %s", job.id, title)
        _signals().heard(ARRIVED)
        return job

    async def note(self, job_id: str, told: dict[str, Any]) -> None:
        async with self._factory() as session:
            await session.execute(update(RenderJob).where(RenderJob.id == job_id).values(told=dict(told)))
            await session.commit()
        job = self._known.get(job_id)
        if job is not None:
            job.told = dict(told)

    async def _withdraw(self, session, row: RenderJob, reason: str) -> None:
        if row.followed:
            row.state = State.WITHDRAWN.value
            row.reason = reason
            row.worker = None
            row.progress = None
        else:
            await session.delete(row)
        await self._tell(session, CHANGED)

    async def withdraw(self, job_id: str, reason: str = "") -> None:
        async with self._factory() as session:
            row = await session.scalar(select(RenderJob).where(RenderJob.id == job_id, RenderJob.state.in_(OPEN)))
            if row is not None:
                await self._withdraw(session, row, reason)
                await session.commit()
        self._ended(job_id, State.WITHDRAWN, reason=reason)
        _signals().heard(CHANGED)

    async def close(self, job_id: str) -> None:
        async with self._factory() as session:
            await session.execute(delete(RenderJob).where(RenderJob.id == job_id))
            await session.commit()
        self._known.pop(job_id, None)

    async def waiting(self, *, now: Optional[float] = None) -> list[Job]:
        await self.sweep(now=now)
        async with self._factory() as session:
            rows = (await session.scalars(
                select(RenderJob).where(RenderJob.state == State.WAITING.value).order_by(RenderJob.created, RenderJob.seq)
            )).all()
            return [self._fresh(row) for row in rows]

    async def ahead_of(self, job: Job, *, now: Optional[float] = None) -> int:
        line = await self.waiting(now=now)
        return next((at for at, other in enumerate(line) if other.id == job.id), 0)

    async def open_for(self, requester: int) -> int:
        async with self._factory() as session:
            return await session.scalar(
                select(func.count()).select_from(RenderJob).where(RenderJob.requester == requester, RenderJob.state.in_(OPEN))
            ) or 0

    async def claim(self, worker: str, name: str = "", *, now: Optional[float] = None,
                    rulesets: frozenset[int] = frozenset({0}),
                    only_for: Optional["Serves"] = None) -> Optional[Job]:
        now = now if now is not None else time.time()
        await self.sweep(now=now)
        async with self._factory() as session:
            rows = (await session.scalars(
                select(RenderJob).where(RenderJob.state == State.WAITING.value, RenderJob.ruleset.in_(list(rulesets)))
                .order_by(RenderJob.created, RenderJob.seq)
            )).all()
            for row in rows:
                if only_for is not None and not self._fresh(row).served_by(only_for):
                    continue
                taken = await session.execute(
                    update(RenderJob).where(RenderJob.seq == row.seq, RenderJob.state == State.WAITING.value)
                    .values(state=State.CLAIMED.value, worker=worker, worker_name=name, lease_until=now + LEASE_SECONDS,
                            progress=None)
                )
                if taken.rowcount != 1:
                    await session.rollback()
                    continue
                await self._tell(session, CHANGED)
                await session.commit()
                await session.refresh(row)
                job = self._fresh(row)
                logger.info("job %s claimed by %s", job.id, name or worker[:8])
                _signals().heard(CHANGED)
                return job
        return None

    async def heartbeat(self, job_id: str, worker: str, progress: Optional[dict[str, Any]] = None, *,
                        now: Optional[float] = None) -> bool:
        values: dict[str, Any] = {"lease_until": (now if now is not None else time.time()) + LEASE_SECONDS}
        if progress is not None:
            values["progress"] = progress
        async with self._factory() as session:
            beaten = await session.execute(
                update(RenderJob).where(RenderJob.id == job_id, RenderJob.state == State.CLAIMED.value,
                                        RenderJob.worker == worker).values(**values)
            )
            await session.commit()
        if beaten.rowcount != 1:
            return False
        job = self._known.get(job_id)
        if job is not None:
            job.lease_until = values["lease_until"]
            if progress is not None:
                job.progress = progress
        return True

    async def _held(self, session, job_id: str, worker: str) -> Optional[RenderJob]:
        return await session.scalar(
            select(RenderJob).where(RenderJob.id == job_id, RenderJob.state == State.CLAIMED.value,
                                    RenderJob.worker == worker).with_for_update()
        )

    async def finish(self, job_id: str, worker: str, payload: dict[str, Any]) -> bool:
        async with self._factory() as session:
            row = await self._held(session, job_id, worker)
            if row is None:
                return False
            if row.followed:
                row.state = State.SETTLED.value
                row.payload = payload
            else:
                await session.delete(row)
            await self._tell(session, CHANGED)
            await session.commit()
        self._ended(job_id, State.SETTLED, payload=payload)
        _signals().heard(CHANGED)
        return True

    async def _back_in_line(self, session, row: RenderJob, who: str, reason: str) -> bool:
        row.attempts = (row.attempts or 0) + 1
        row.troubles = list(row.troubles or []) + [f"{who or '?'}: {reason}"]
        row.state = State.WAITING.value
        row.worker = None
        row.worker_name = ""
        row.lease_until = 0.0
        row.progress = None
        row.reason = reason
        _trouble(self._fresh(row), who, reason)
        if row.attempts >= MAX_ATTEMPTS:
            await self._withdraw(session, row, reason)
            return True
        await self._tell(session, ARRIVED)
        return False

    async def give_back(self, job_id: str, worker: str, reason: str, *, now: Optional[float] = None) -> bool:
        async with self._factory() as session:
            row = await self._held(session, job_id, worker)
            if row is None:
                return False
            who = row.worker_name or worker[:8]
            logger.info("job %s given back by %s (attempt %d): %s", job_id, who, (row.attempts or 0) + 1, reason)
            over = await self._back_in_line(session, row, who, reason)
            await session.commit()
        if over:
            self._ended(job_id, State.WITHDRAWN, reason=reason)
        _signals().heard(CHANGED if over else ARRIVED)
        return True

    async def sweep(self, *, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        over: list[tuple[str, str]] = []
        async with self._factory() as session:
            quiet = (await session.scalars(
                select(RenderJob).where(RenderJob.state == State.CLAIMED.value, RenderJob.lease_until <= now).with_for_update()
            )).all()
            for row in quiet:
                who = row.worker_name or (row.worker or "")[:8]
                logger.warning("job %s lost its worker %s", row.id, row.worker_name or row.worker)
                if await self._back_in_line(session, row, who, "the worker went quiet"):
                    over.append((row.id, "the worker went quiet"))
            old = (await session.scalars(
                select(RenderJob).where(RenderJob.state.in_(OPEN), RenderJob.created < now - MAX_AGE_SECONDS).with_for_update()
            )).all()
            for row in old:
                if row.state not in OPEN:
                    continue
                logger.warning("job %s expired unrendered", row.id)
                await self._withdraw(session, row, "expired")
                over.append((row.id, "expired"))
            stale = (await session.scalars(
                select(RenderJob).where(RenderJob.state.in_(CLOSED), RenderJob.created < now - CLOSE_AFTER)
            )).all()
            for row in stale:
                _leftovers(row)
                await session.delete(row)
            if quiet or old or stale:
                await session.commit()
        for job_id, reason in over:
            self._ended(job_id, State.WITHDRAWN, reason=reason)
        if quiet or old:
            _signals().heard(ARRIVED)
            _signals().heard(CHANGED)

    async def rendering(self) -> dict[str, Job]:
        async with self._factory() as session:
            rows = (await session.scalars(
                select(RenderJob).where(RenderJob.state == State.CLAIMED.value, RenderJob.worker.is_not(None))
            )).all()
            return {row.worker: self._fresh(row) for row in rows}

    async def get(self, job_id: str) -> Optional[Job]:
        async with self._factory() as session:
            row = await session.scalar(select(RenderJob).where(RenderJob.id == job_id, RenderJob.state.in_(OPEN)))
            return self._fresh(row) if row is not None else None

    async def followed(self) -> list[Job]:
        async with self._factory() as session:
            rows = (await session.scalars(
                select(RenderJob).where(RenderJob.followed.is_(True)).order_by(RenderJob.created, RenderJob.seq)
            )).all()
            return [self._fresh(row) for row in rows]

    async def refresh(self, job: Job) -> Job:
        if job.settled.is_set():
            return job
        self._known.setdefault(job.id, job)
        async with self._factory() as session:
            row = await session.scalar(select(RenderJob).where(RenderJob.id == job.id))
            if row is not None:
                return self._fresh(row)
        self._ended(job.id, State.WITHDRAWN, reason=job.reason or "gone")
        return job

    async def wait(self, job: Job, most: float) -> Job:
        signals = _signals()
        signals.listen(CHANGED)
        mark = signals.mark(CHANGED)
        if not job.settled.is_set():
            settled = asyncio.create_task(job.settled.wait())
            changed = asyncio.create_task(mark.wait())
            await asyncio.wait({settled, changed}, timeout=most, return_when=asyncio.FIRST_COMPLETED)
            settled.cancel()
            changed.cancel()
        return await self.refresh(job)

    async def for_video(self, video_id: int) -> Optional[Job]:
        await self.sweep()
        async with self._factory() as session:
            row = await session.scalar(select(RenderJob).where(RenderJob.video_id == video_id, RenderJob.state.in_(OPEN)))
            return self._fresh(row) if row is not None else None

queue = RenderQueue()
