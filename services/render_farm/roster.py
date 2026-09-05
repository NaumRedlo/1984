from dataclasses import dataclass
from time import monotonic
from typing import Any, Optional

from utils.logger import get_logger

logger = get_logger("services.render_farm.roster")

GONE_AFTER = 60.0

@dataclass
class Worker:

    name: str
    first_seen: float
    last_seen: float

    build: Optional[str] = None
    take: bool = True
    reason: str = ""

    code: str = ""
    detail: str = ""
    threads: int = 0
    polite: bool = False
    delivered: int = 0
    handed_back: int = 0

    def state(self, *, rendering: bool) -> str:
        if rendering:
            return "rendering"
        return "ready" if self.take else "resting"

class Roster:

    def __init__(self) -> None:
        self._seen: dict[str, Worker] = {}

    def hello(self, name: str, *, build: Optional[str] = None,
              capacity: Optional[dict[str, Any]] = None,
              now: Optional[float] = None) -> Worker:
        now = now if now is not None else monotonic()
        worker = self._seen.get(name)
        if worker is None:
            worker = Worker(name=name, first_seen=now, last_seen=now)
            self._seen[name] = worker
            logger.info("worker %s joined the farm", name)
        worker.last_seen = now

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
        now = now if now is not None else monotonic()
        for name, worker in list(self._seen.items()):
            if now - worker.last_seen > GONE_AFTER:
                logger.info("worker %s left the farm", name)
                del self._seen[name]
        return sorted(self._seen.values(), key=lambda w: w.first_seen)

    def __len__(self) -> int:
        return len(self._seen)

roster = Roster()

__all__ = ["Roster", "Worker", "roster", "GONE_AFTER"]
