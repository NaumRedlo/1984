import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select

from db.models.duel import DuelAudit, DuelModelVersion
from services.duels import balance, kamerton
from utils.timeutils import utcnow

NAME = "kamerton"
NEW_LABELS_LEAST = 50
EVERY = timedelta(days=1)
HELD_OUT = 5
busy = asyncio.Lock()

def held_out(set_id: int) -> bool:
    return int(hashlib.sha256(str(set_id).encode()).hexdigest(), 16) % HELD_OUT == 0

async def unchanged(factory) -> bool:
    found = await kamerton.examples(factory)
    async with factory() as session:
        last = await session.scalar(select(DuelModelVersion).where(DuelModelVersion.version.like(f"{NAME}-%"))
                                    .order_by(DuelModelVersion.created_at.desc()))
    return last is not None and (last.report or {}).get("fingerprint") == kamerton.fingerprint(found)

def _people(found: list[kamerton.Example]) -> int:
    return sum(1 for example in found if example.verified)

async def _number(session) -> int:
    names = list((await session.scalars(select(DuelModelVersion.version).where(DuelModelVersion.version.like(f"{NAME}-%")))).all())
    names += (await session.scalars(select(DuelAudit.target).where(DuelAudit.action == "delete_model", DuelAudit.target.like(f"{NAME}-%")))).all()
    return max((int(name.split("-", 1)[1]) for name in names if name.split("-", 1)[1].isdigit()), default=0) + 1

async def train(factory, steps: int = 400, now: Optional[datetime] = None) -> Optional[tuple[str, dict[str, Any]]]:
    async with busy:
        return await _train(factory, steps, now)

async def _train(factory, steps: int, now: Optional[datetime]) -> Optional[tuple[str, dict[str, Any]]]:
    now = now or utcnow()
    examples = await kamerton.examples(factory)
    if len({example.slot for example in examples}) < 2:
        return None
    printed = kamerton.fingerprint(examples)
    async with factory() as session:
        last = await session.scalar(select(DuelModelVersion).where(DuelModelVersion.version.like(f"{NAME}-%"))
                                    .order_by(DuelModelVersion.created_at.desc()))
    if last is not None and (last.report or {}).get("fingerprint") == printed:
        return None
    judged, years = [], {}
    folds = [(str(year), [example for example in examples if not example.own and not example.decided and example.year == year])
             for year in sorted({example.year for example in examples if not example.own and not example.decided})]
    folds.append(("queue", [example for example in examples if example.decided and held_out(example.set_id)]))
    for name, held in folds:
        if not held:
            continue
        sets = {example.set_id for example in held}
        rest = [example for example in examples if example.set_id not in sets]
        if len({example.slot for example in rest}) < 2:
            continue
        model = await asyncio.to_thread(kamerton.StackedModel.fit, rest, 0.01, steps)
        part = kamerton.judged_on(model, held)
        years[name] = kamerton.summary(part)
        judged += part
    model = await asyncio.to_thread(kamerton.StackedModel.fit, examples, 0.01, steps)
    async with factory() as session:
        number = await _number(session)
    version = f"{NAME}-{number}"
    model.version = version
    corpus = [example for example in examples if not example.decided]
    found_report = {"name": NAME, "number": number, "kind": "stacked", "maps": len(examples), "corpus": len(corpus),
                    "own": sum(1 for example in corpus if example.own), "queue": len(examples) - len(corpus),
                    "secondary": sum(1 for example in examples if example.secondary), "verified": sum(1 for example in corpus if example.verified),
                    "test": kamerton.summary(judged), "years": years, "fingerprint": printed}
    await balance.train(factory, version, model.dump(), found_report, _people(examples), now)
    return version, found_report

async def maybe_train(factory, now: Optional[datetime] = None, steps: int = 400) -> Optional[str]:
    if busy.locked():
        return None
    now = now or utcnow()
    async with factory() as session:
        last = await session.scalar(select(DuelModelVersion).order_by(DuelModelVersion.created_at.desc()))
    if last is not None and now - last.created_at < EVERY:
        return None
    before = last.labels if last is not None and last.version.startswith(f"{NAME}-") else 0
    if _people(await kamerton.examples(factory)) - before < NEW_LABELS_LEAST:
        return None
    made = await train(factory, steps, now)
    return made[0] if made is not None else None

JOBS_KEPT = 20
jobs: dict[str, dict[str, Any]] = {}

def open_job(now: Optional[datetime] = None) -> str:
    for old in [name for name, job in jobs.items() if job["state"] != "running"][:max(0, len(jobs) + 1 - JOBS_KEPT)]:
        del jobs[old]
    name = uuid.uuid4().hex[:12]
    jobs[name] = {"job": name, "state": "running", "version": None, "error": None, "started_at": (now or utcnow()).isoformat(), "finished_at": None}
    return name

def running_job() -> Optional[str]:
    return next((name for name, job in jobs.items() if job["state"] == "running"), None)

async def run_job(factory, name: str, steps: int = 400) -> Optional[tuple[str, dict[str, Any]]]:
    job = jobs[name]
    try:
        made = await train(factory, steps)
    except Exception as error:
        job.update(state="failed", error=f"{type(error).__name__}: {error}"[:500], finished_at=utcnow().isoformat())
        raise
    job.update(state="done" if made is not None else "nothing_new", version=made[0] if made is not None else None, finished_at=utcnow().isoformat())
    return made
