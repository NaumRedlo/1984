"""Who is out there, and what they are doing.

The farm has never been able to answer that. Jobs are tracked, and a job knows
which worker holds it, but a machine that is sitting there ready — or one that
is present and declining because it is on battery — leaves no trace at all. So
"is anybody rendering for us tonight" was answered by reading the log
backwards, and "why is everything rendering on the server" had no answer short
of asking people.

Almost nothing had to be collected for this. A worker already announces itself
on every poll: its name is in a header and its engine build is in the body,
once a second. This is the part that remembers.

## Present, and taking work, are different questions

A worker on a draining battery stops claiming, which used to make it invisible
— indistinguishable from a laptop that was shut. It now says hello anyway and
says why it is not taking anything, because "three machines are here and all of
them are on battery" and "nobody is here" call for opposite reactions from
whoever is looking.

## Why the counts are not persisted

They are what this process has seen. A restarted bot shows an empty farm
filling up again over the next second, which is honest — it does not know what
happened before it, and a number carried across a restart would claim it did.
"""

from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Optional

from utils.logger import get_logger

logger = get_logger("services.render_farm.roster")

# Silence longer than this and a worker is treated as gone. A worker polls
# every second when it is taking work and every fifteen when it is resting, and
# it says hello from inside a render too — so a minute of nothing is a machine
# that is off, not one that is busy.
GONE_AFTER = 60.0


@dataclass
class Worker:
    """One machine, as the farm has seen it."""

    name: str
    first_seen: float
    last_seen: float
    # What its engine says it is. Kept from the last claim that carried one:
    # a heartbeat says the worker is alive without repeating its build.
    build: Optional[str] = None
    take: bool = True
    reason: str = ""
    # Why, as a word rather than a sentence — see `machine.Capacity.code`. The
    # sentence is written on the worker in English and read here by somebody
    # in another language; this is the half that can be translated. Empty from
    # a worker too old to send it, and then the sentence stands.
    code: str = ""
    detail: str = ""
    threads: int = 0
    polite: bool = False
    delivered: int = 0
    handed_back: int = 0

    def state(self, *, rendering: bool) -> str:
        """One word for what this machine is doing.

        Rendering beats everything: a worker that took a job before its battery
        dropped is still rendering, and reporting it as resting would be
        reporting the wrong machine as available.
        """
        if rendering:
            return "rendering"
        return "ready" if self.take else "resting"


class Roster:
    """Every worker this process has heard from lately."""

    def __init__(self) -> None:
        self._seen: dict[str, Worker] = {}

    def hello(self, name: str, *, build: Optional[str] = None,
              capacity: Optional[dict[str, Any]] = None,
              now: Optional[float] = None) -> Worker:
        """Record that this worker is alive, and what it said about itself.

        Called from every endpoint a worker touches rather than from one of
        them, because the endpoints a busy worker uses are not the ones an idle
        worker uses — and a farm view that lost a machine the moment it started
        working would be showing exactly the wrong thing.
        """
        now = now if now is not None else monotonic()
        worker = self._seen.get(name)
        if worker is None:
            worker = Worker(name=name, first_seen=now, last_seen=now)
            self._seen[name] = worker
            logger.info("worker %s joined the farm", name)
        worker.last_seen = now
        # Only overwritten when there is something to overwrite it with. A
        # heartbeat carries neither, and blanking a build on every heartbeat
        # would make the farm view flicker between knowing and not.
        if build:
            worker.build = build
        if capacity:
            worker.take = bool(capacity.get("take", True))
            worker.reason = str(capacity.get("reason") or "")
            worker.code = str(capacity.get("code") or "")
            worker.detail = str(capacity.get("detail") or "")
            worker.threads = int(capacity.get("threads") or 0)
            worker.polite = bool(capacity.get("polite"))
        return worker

    def delivered(self, name: str) -> None:
        worker = self._seen.get(name)
        if worker:
            worker.delivered += 1

    def handed_back(self, name: str) -> None:
        worker = self._seen.get(name)
        if worker:
            worker.handed_back += 1

    def here(self, *, now: Optional[float] = None) -> list[Worker]:
        """Everyone still around, longest-serving first.

        Sweeping happens here rather than on a timer: the only moment a
        departure matters is the moment somebody looks, and a farm with no
        watcher has no reason to be tidying itself.
        """
        now = now if now is not None else monotonic()
        for name, worker in list(self._seen.items()):
            if now - worker.last_seen > GONE_AFTER:
                logger.info("worker %s left the farm", name)
                del self._seen[name]
        return sorted(self._seen.values(), key=lambda w: w.first_seen)

    def __len__(self) -> int:
        return len(self._seen)


# One per bot process, like the queue beside it.
roster = Roster()

__all__ = ["Roster", "Worker", "roster", "GONE_AFTER"]
