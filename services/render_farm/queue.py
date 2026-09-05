import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum
from time import monotonic
from typing import Any, Optional

from utils.logger import get_logger

logger = get_logger("services.render_farm.queue")

LEASE_SECONDS = 90.0

MAX_AGE_SECONDS = 3600.0

MAX_ATTEMPTS = 2

class State(str, Enum):
    WAITING = "waiting"
    CLAIMED = "claimed"
    SETTLED = "settled"

@dataclass
class Job:

    id: str
    replay_path: str
    title: str

    settings: dict[str, Any]

    assets: dict[str, str]
    created: float
    state: State = State.WAITING
    worker: Optional[str] = None
    lease_until: float = 0.0

    attempts: int = 0

    progress: Optional[dict[str, Any]] = None

    settled: asyncio.Event = field(default_factory=asyncio.Event)

    taken: asyncio.Event = field(default_factory=asyncio.Event)
    payload: Optional[dict[str, Any]] = None

    withdrawn: bool = False

class RenderQueue:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def offer(self, replay_path: str, title: str, settings: dict[str, Any], *,
              assets: Optional[dict[str, str]] = None,
              now: Optional[float] = None) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:16],
            replay_path=replay_path,
            title=title,
            settings=dict(settings),
            assets=dict(assets or {}),
            created=now if now is not None else monotonic(),
        )
        self._jobs[job.id] = job
        return job

    def withdraw(self, job_id: str) -> None:
        job = self._jobs.pop(job_id, None)
        if job:
            job.withdrawn = True
            job.settled.set()

    def waiting(self, *, now: Optional[float] = None) -> list[Job]:
        self.sweep(now=now)
        return [j for j in self._jobs.values() if j.state is State.WAITING]

    def claim(self, worker: str, *, now: Optional[float] = None) -> Optional[Job]:
        now = now if now is not None else monotonic()
        self.sweep(now=now)
        pending = sorted(
            (j for j in self._jobs.values()
             if j.state is State.WAITING and j.attempts < MAX_ATTEMPTS),
            key=lambda j: j.created,
        )
        if not pending:
            return None
        job = pending[0]
        job.state = State.CLAIMED
        job.worker = worker
        job.lease_until = now + LEASE_SECONDS
        job.taken.set()
        logger.info("job %s claimed by %s", job.id, worker)
        return job

    def heartbeat(self, job_id: str, worker: str, progress: Optional[dict] = None, *,
                  now: Optional[float] = None) -> bool:
        job = self._held_by(job_id, worker)
        if job is None:
            return False
        job.lease_until = (now if now is not None else monotonic()) + LEASE_SECONDS
        if progress is not None:
            job.progress = progress
        return True

    def finish(self, job_id: str, worker: str, payload: dict[str, Any]) -> bool:
        job = self._held_by(job_id, worker)
        if job is None:
            return False
        job.state = State.SETTLED
        job.payload = payload
        job.settled.set()
        self._jobs.pop(job_id, None)
        return True

    def give_back(self, job_id: str, worker: str, reason: str, *,
                  now: Optional[float] = None) -> bool:
        job = self._held_by(job_id, worker)
        if job is None:
            return False
        job.attempts += 1
        logger.info("job %s given back by %s (attempt %d): %s",
                    job_id, worker, job.attempts, reason)
        job.state = State.WAITING
        job.worker = None
        job.lease_until = 0.0
        return True

    def sweep(self, *, now: Optional[float] = None) -> None:
        now = now if now is not None else monotonic()
        for job in list(self._jobs.values()):
            if job.state is State.CLAIMED and now >= job.lease_until:
                logger.warning("job %s lost its worker %s", job.id, job.worker)

                job.attempts += 1
                job.state = State.WAITING
                job.worker = None
                job.lease_until = 0.0
            if now - job.created > MAX_AGE_SECONDS:
                logger.warning("job %s expired unrendered", job.id)
                self.withdraw(job.id)

    def rendering(self) -> set[str]:
        return {job.worker for job in self._jobs.values()
                if job.state is State.CLAIMED and job.worker}

    def _held_by(self, job_id: str, worker: str) -> Optional[Job]:
        job = self._jobs.get(job_id)
        if job is None or job.state is not State.CLAIMED or job.worker != worker:
            return None
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

queue = RenderQueue()
