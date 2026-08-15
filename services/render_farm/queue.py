"""Renders waiting for a machine to do them.

The bot offers a render here and waits. A worker claims it, renders it, and
posts the file back. If no worker claims it in time — the laptop is asleep, or
nobody is running one — the bot takes it back and renders it itself, which is
what it did before this existed and is what it must keep doing when this is
unavailable. A feature that makes renders *stop working* when the laptop is
shut is not worth the speed.

Claims are leases, not handovers. A worker that dies mid-render — a reboot, a
snapped Wi-Fi link, a battery that hit the floor — would otherwise take the job
with it and leave somebody watching a progress bar that will never move again.
So a claim expires unless the worker keeps saying it is alive, and an expired
claim puts the job back where it came from.

In memory, like `bot.handlers.dossier.renders`, and for the same reason: these
are one tester's experiments, and a queue that survived restarts would mean
replays on disk for ever with nothing to prune them.
"""

import asyncio
import uuid
from dataclasses import dataclass, field
from enum import Enum
from time import monotonic
from typing import Any, Optional

from utils.logger import get_logger

logger = get_logger("services.render_farm.queue")

# How long a claim is good for without word from the worker. Well over the
# heartbeat interval, because a worker that is merely busy encoding must not
# lose a render it is halfway through to a slow reply.
LEASE_SECONDS = 90.0

# Nothing may sit here for ever. Past this a job is dropped even if it was
# never claimed, so an offer whose waiter has gone away cannot pin its files.
MAX_AGE_SECONDS = 3600.0


class State(str, Enum):
    WAITING = "waiting"
    CLAIMED = "claimed"
    SETTLED = "settled"


@dataclass
class Job:
    """One render, and who currently owes it."""

    id: str
    replay_path: str
    title: str
    # Everything `services.dossier.runner.video` would have been called with,
    # passed through untouched. The worker sends it straight back to the same
    # function on its own machine, so the two hosts cannot drift apart on what
    # a render *is* — only on where it happens.
    settings: dict[str, Any]
    created: float
    state: State = State.WAITING
    worker: Optional[str] = None
    lease_until: float = 0.0
    # The last thing the worker said about how far along it is, so the bot can
    # keep editing its message while somebody else does the work.
    progress: Optional[dict[str, Any]] = None
    # An Event rather than a Future: a Future binds an event loop the moment it
    # is built, and a job can be offered from anywhere. This one binds nothing
    # until something awaits it.
    settled: asyncio.Event = field(default_factory=asyncio.Event)
    # Waited on separately from `settled`, because the two deadlines are
    # nothing alike: a worker either takes the job in seconds or is not there,
    # while rendering it afterwards is minutes of honest work.
    taken: asyncio.Event = field(default_factory=asyncio.Event)
    payload: Optional[dict[str, Any]] = None
    # Set when the bot took the job back. The waiter has to tell "the worker
    # finished" from "stop waiting, I am doing it myself" — they wake the same
    # way and mean opposite things.
    withdrawn: bool = False


class RenderQueue:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}

    # ── the bot's side ────────────────────────────────────────────────────

    def offer(self, replay_path: str, title: str, settings: dict[str, Any], *,
              now: Optional[float] = None) -> Job:
        job = Job(
            id=uuid.uuid4().hex[:16],
            replay_path=replay_path,
            title=title,
            settings=dict(settings),
            created=now if now is not None else monotonic(),
        )
        self._jobs[job.id] = job
        return job

    def withdraw(self, job_id: str) -> None:
        """Take a job back — the bot has decided to render it itself.

        Forgotten rather than marked, so a worker that claimed it and is still
        going gets a plain "not yours" on its next word and stops. The bot is
        already rendering by then; two copies of the same video is the failure
        this prevents.
        """
        job = self._jobs.pop(job_id, None)
        if job:
            job.withdrawn = True
            job.settled.set()

    def waiting(self, *, now: Optional[float] = None) -> list[Job]:
        self.sweep(now=now)
        return [j for j in self._jobs.values() if j.state is State.WAITING]

    # ── a worker's side ───────────────────────────────────────────────────

    def claim(self, worker: str, *, now: Optional[float] = None) -> Optional[Job]:
        """Hand the oldest waiting job to `worker`, or nothing if there is none.

        Oldest first, because the person who has been watching a progress bar
        longest is the one to serve next.
        """
        now = now if now is not None else monotonic()
        self.sweep(now=now)
        pending = sorted(
            (j for j in self._jobs.values() if j.state is State.WAITING),
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
        """Extend the claim. False means the job is no longer this worker's —
        it expired, or the bot took it back — and the worker should stop."""
        job = self._held_by(job_id, worker)
        if job is None:
            return False
        job.lease_until = (now if now is not None else monotonic()) + LEASE_SECONDS
        if progress is not None:
            job.progress = progress
        return True

    def finish(self, job_id: str, worker: str, payload: dict[str, Any]) -> bool:
        """The worker has the video. False if the job was not theirs to finish."""
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
        """The worker cannot do it after all — the battery hit the floor, the
        engine failed, the map would not download.

        Back to waiting rather than failed: another worker may be able to, and
        if none is, the bot's own timeout is what turns this into a local
        render. The worker's own opinion of why must not become the user's
        error message.
        """
        job = self._held_by(job_id, worker)
        if job is None:
            return False
        logger.info("job %s given back by %s: %s", job_id, worker, reason)
        job.state = State.WAITING
        job.worker = None
        job.lease_until = 0.0
        return True

    # ── upkeep ────────────────────────────────────────────────────────────

    def sweep(self, *, now: Optional[float] = None) -> None:
        """Expired leases go back to waiting; ancient jobs go away entirely."""
        now = now if now is not None else monotonic()
        for job in list(self._jobs.values()):
            if job.state is State.CLAIMED and now >= job.lease_until:
                logger.warning("job %s lost its worker %s", job.id, job.worker)
                job.state = State.WAITING
                job.worker = None
                job.lease_until = 0.0
            if now - job.created > MAX_AGE_SECONDS:
                logger.warning("job %s expired unrendered", job.id)
                self.withdraw(job.id)

    def _held_by(self, job_id: str, worker: str) -> Optional[Job]:
        job = self._jobs.get(job_id)
        if job is None or job.state is not State.CLAIMED or job.worker != worker:
            return None
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)


# One per bot process, like the render lock beside it.
queue = RenderQueue()
