"""Replays waiting to be rendered.

Judging takes a second; rendering takes minutes. So the bot judges first, then
offers a button — and the replay file has to outlive the handler that received
it for that button to mean anything.

The store is deliberately small and in-memory. These are scratch files for one
tester's experiments, not data: losing them on restart costs a re-upload, while
keeping them would leave replays on disk for ever with nothing to prune them.
"""

import asyncio
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from time import monotonic

from utils.logger import get_logger

logger = get_logger("bot.dossier.renders")

# Enough for a few experiments in flight; past that the oldest go.
_MAX_PENDING = 12

# One render at a time. Two encoders on one host don't finish twice as fast,
# they finish twice as slowly and compete for the same cores.
render_lock = asyncio.Lock()


@dataclass
class Pending:
    replay_path: str
    workdir: str
    title: str
    created: float = field(default_factory=monotonic)
    # What the engine said about the render, once there has been one. Kept so
    # the summary can live behind a button instead of on top of the video: it
    # is worth reading afterwards and worth nothing in the way.
    report: list[str] = field(default_factory=list)
    # The judge's whole answer. The read-out is split across buttons now, and
    # each section is drawn on demand from this rather than from a string built
    # once and sliced — a section that disagrees with the totals above it would
    # be worse than no section.
    verdict: dict = field(default_factory=dict)
    # The render in flight, so it can be called off. A render is minutes on one
    # core and the wrong size is a mistake worth interrupting.
    task: asyncio.Task | None = None


# How a render is set up. Per user rather than per replay: someone who renders
# at 30fps once wants it next time too, and re-picking it for every file is the
# kind of friction that makes people stop using a tool.
@dataclass
class Choices:
    size: str = "1280x720"
    fps: int = 60
    mute: bool = False
    # None means whatever the deployment configured, which is the right default
    # for a setting most people will never touch.
    skin: str | None = None

    def summary(self) -> str:
        sound = "без звука" if self.mute else "со звуком"
        return f"{self.size} · {self.fps} fps · {sound} · скин {self.skin or 'по умолчанию'}"


_pending: dict[str, Pending] = {}
_choices: dict[int, Choices] = {}


def choices(user_id: int) -> Choices:
    """This user's render settings, created on first use."""
    return _choices.setdefault(user_id, Choices())


def remember(replay_path: str, title: str, verdict: dict | None = None) -> str:
    """Copy the replay somewhere it will survive, and return a token for it."""
    workdir = tempfile.mkdtemp(prefix="dossier-render-")
    kept = os.path.join(workdir, "replay.osr")
    shutil.copyfile(replay_path, kept)

    token = uuid.uuid4().hex[:12]
    _pending[token] = Pending(
        replay_path=kept, workdir=workdir, title=title, verdict=verdict or {}
    )
    _evict_old()
    return token


def get(token: str) -> Pending | None:
    return _pending.get(token)


def forget(token: str) -> None:
    entry = _pending.pop(token, None)
    if entry:
        shutil.rmtree(entry.workdir, ignore_errors=True)


def _evict_old() -> None:
    while len(_pending) > _MAX_PENDING:
        oldest = min(_pending, key=lambda t: _pending[t].created)
        logger.info("evicting pending render %s", oldest)
        forget(oldest)
