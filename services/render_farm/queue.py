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
MAX_ATTEMPTS = 3

STANDARD = {"width": 1920, "height": 1080, "fps": 60, "loudness": -14.0}

class State(str, Enum):
    WAITING = "waiting"
    CLAIMED = "claimed"
    SETTLED = "settled"

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

    def handed(self) -> dict[str, Any]:
        skin = None
        if self.skin:
            skin = {"name": self.skin["name"], "hash": self.skin["hash"], "size": self.skin.get("size", 0)}
        return {
            "id": self.id,
            "title": self.title,
            "beatmap_md5": self.beatmap_md5,
            "beatmapset_id": self.beatmapset_id,
            "settings": dict(STANDARD),
            "skin": skin,
            "lease_seconds": LEASE_SECONDS,
        }

class RenderQueue:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    def offer(self, replay_path: str, title: str, *, beatmap_md5: str, beatmapset_id: Optional[int] = None,
              skin: Optional[dict[str, Any]] = None, requester: int = 0, chat_id: int = 0,
              now: Optional[float] = None) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:16],
            replay_path=replay_path,
            title=title,
            beatmap_md5=beatmap_md5,
            beatmapset_id=beatmapset_id,
            skin=dict(skin) if skin else None,
            requester=requester,
            chat_id=chat_id,
            created=now if now is not None else monotonic(),
        )
        self._jobs[job.id] = job
        logger.info("job %s offered: %s", job.id, title)
        return job

    def withdraw(self, job_id: str, reason: str = "") -> None:
        job = self._jobs.pop(job_id, None)
        if job:
            job.withdrawn = True
            job.reason = reason
            job.settled.set()

    def waiting(self, *, now: Optional[float] = None) -> list[Job]:
        self.sweep(now=now)
        return sorted((j for j in self._jobs.values() if j.state is State.WAITING), key=lambda j: j.created)

    def ahead_of(self, job: Job, *, now: Optional[float] = None) -> int:
        line = self.waiting(now=now)
        return next((at for at, other in enumerate(line) if other.id == job.id), 0)

    def open_for(self, requester: int) -> int:
        return sum(1 for j in self._jobs.values() if j.requester == requester)

    def claim(self, worker: str, name: str = "", *, now: Optional[float] = None) -> Optional[Job]:
        now = now if now is not None else monotonic()
        self.sweep(now=now)
        pending = sorted((j for j in self._jobs.values() if j.state is State.WAITING), key=lambda j: j.created)
        if not pending:
            return None
        job = pending[0]
        job.state = State.CLAIMED
        job.worker = worker
        job.worker_name = name
        job.lease_until = now + LEASE_SECONDS
        job.progress = None
        logger.info("job %s claimed by %s", job.id, name or worker[:8])
        return job

    def heartbeat(self, job_id: str, worker: str, progress: Optional[dict[str, Any]] = None, *,
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
        self._jobs.pop(job_id, None)
        job.settled.set()
        return True

    def give_back(self, job_id: str, worker: str, reason: str, *, now: Optional[float] = None) -> bool:
        job = self._held_by(job_id, worker)
        if job is None:
            return False
        job.attempts += 1
        logger.info("job %s given back by %s (attempt %d): %s", job_id, job.worker_name or worker[:8], job.attempts, reason)
        self._back_in_line(job, reason)
        return True

    def sweep(self, *, now: Optional[float] = None) -> None:
        now = now if now is not None else monotonic()
        for job in list(self._jobs.values()):
            if job.state is State.CLAIMED and now >= job.lease_until:
                logger.warning("job %s lost its worker %s", job.id, job.worker_name or job.worker)
                job.attempts += 1
                self._back_in_line(job, "the worker went quiet")
            if job.id in self._jobs and now - job.created > MAX_AGE_SECONDS:
                logger.warning("job %s expired unrendered", job.id)
                self.withdraw(job.id, "expired")

    def rendering(self) -> dict[str, Job]:
        return {job.worker: job for job in self._jobs.values() if job.state is State.CLAIMED and job.worker}

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def _back_in_line(self, job: Job, reason: str) -> None:
        job.state = State.WAITING
        job.worker = None
        job.worker_name = ""
        job.lease_until = 0.0
        job.progress = None
        job.reason = reason
        if job.attempts >= MAX_ATTEMPTS:
            self.withdraw(job.id, reason)

    def _held_by(self, job_id: str, worker: str) -> Optional[Job]:
        job = self._jobs.get(job_id)
        if job is None or job.state is not State.CLAIMED or job.worker != worker:
            return None
        return job

queue = RenderQueue()
