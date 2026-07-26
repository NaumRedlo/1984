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


_pending: dict[str, Pending] = {}


def remember(replay_path: str, title: str) -> str:
    """Copy the replay somewhere it will survive, and return a token for it."""
    workdir = tempfile.mkdtemp(prefix="dossier-render-")
    kept = os.path.join(workdir, "replay.osr")
    shutil.copyfile(replay_path, kept)

    token = uuid.uuid4().hex[:12]
    _pending[token] = Pending(replay_path=kept, workdir=workdir, title=title)
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
