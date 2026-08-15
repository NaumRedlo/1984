"""Render this somewhere — a worker if one is listening, here if not.

Drop-in for `services.dossier.runner.video`: same arguments, same return, so
the handler that renders a replay does not learn which machine did it. That is
the whole point of the shape. The engine's own contract — a command line in, a
stream of events out, a file on disk — is identical on both hosts, and the only
question this module answers is which host runs it.

Falling back is not an error path here, it is the ordinary one. The laptop is
somebody's laptop: shut, on battery, in low power mode, or simply not running a
worker. Every one of those has to end with a rendered video, a few seconds
later than it might have been.
"""

import asyncio
import os
import shutil
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any, Optional

from config.settings import RENDER_WORKER_TOKEN, RENDER_WORKER_WAIT
from services.dossier import runner
from services.dossier.runner import Progress, RenderResult
from services.render_farm.queue import State, queue
from utils.logger import get_logger

logger = get_logger("services.render_farm.dispatch")

# How often the waiter looks at the job. Polling rather than more events: the
# states it has to tell apart — claimed, given back, lease expired, finished,
# withdrawn — do not map onto one flag apiece, and a quarter of a second of
# latency is invisible next to a render.
_TICK = 0.25


def _progress_of(raw: dict[str, Any]) -> Optional[Progress]:
    try:
        clip = raw.get("clip")
        return Progress(
            done=int(raw["done"]), total=int(raw["total"]),
            fps=float(raw.get("fps") or 0.0),
            seconds_left=float(raw.get("seconds_left") or 0.0),
            clip=tuple(clip) if clip else None,
        )
    except (KeyError, TypeError, ValueError):
        return None


async def _wait_for_worker(
    job,
    timeout: float,
    on_progress: Optional[Callable[[Progress], Awaitable[None]]],
) -> Optional[dict[str, Any]]:
    """Watch a job until a worker finishes it or it becomes clear none will.

    None means "render it here". The two ways to get there are nobody claiming
    it, and somebody claiming it and then going quiet — a laptop that closed
    its lid mid-render looks exactly like a laptop that never answered, and
    both have to end the same way.
    """
    started = monotonic()
    unclaimed_since: Optional[float] = started
    last_progress = None

    while True:
        # Before the check for a finished job, not after it. A worker sends its
        # last progress and the file itself moments apart, and a loop that
        # returned first would drop every update that shared a tick with the
        # result — which, on a render short enough to fit in one tick, is all
        # of them.
        if on_progress and job.progress and job.progress != last_progress:
            last_progress = job.progress
            told = _progress_of(job.progress)
            if told:
                await on_progress(told)

        if job.settled.is_set():
            return None if job.withdrawn else job.payload

        queue.sweep()
        now = monotonic()
        if job.state is State.WAITING:
            # A job that was claimed and came back starts this clock again, so
            # a worker that dies costs one more wait rather than the whole
            # render timeout.
            unclaimed_since = unclaimed_since or now
            if now - unclaimed_since > RENDER_WORKER_WAIT:
                logger.info("job %s: nobody took it, rendering here", job.id)
                return None
        else:
            unclaimed_since = None

        if now - started > timeout:
            logger.warning("job %s: worker overran, rendering here", job.id)
            return None

        await asyncio.sleep(_TICK)


async def video(
    replay_path: str,
    songs_dir: str,
    out_path: str,
    *,
    title: str = "",
    size: str = "1280x720",
    fps: int = 60,
    mute: bool = False,
    skin: Optional[str] = None,
    leaderboard: Optional[str] = None,
    my_pictures: tuple[Optional[str], Optional[str]] = (None, None),
    on_progress: Optional[Callable[[Progress], Awaitable[None]]] = None,
) -> RenderResult:
    settings = {"size": size, "fps": fps, "mute": mute, "skin": skin}

    # The scoreboard and the player's own pictures are files on *this* host, and
    # sending them is a second transfer for a feature a worker need not have.
    # A remote render simply goes without them rather than going wrong.
    remote_possible = bool(RENDER_WORKER_TOKEN) and not leaderboard

    if remote_possible:
        job = queue.offer(replay_path, title, settings)
        try:
            payload = await _wait_for_worker(job, runner._VIDEO_TIMEOUT_SECONDS, on_progress)
        finally:
            queue.withdraw(job.id)

        if payload:
            produced = payload.get("path")
            if produced and os.path.isfile(produced):
                # Moved, not copied: the upload already wrote it once.
                shutil.move(produced, out_path)
                meta = payload.get("meta") or {}
                logger.info("job %s rendered by %s", job.id, job.worker)
                return RenderResult(
                    report=list(meta.get("report") or []),
                    width=meta.get("width"),
                    height=meta.get("height"),
                    duration=meta.get("duration"),
                )
            logger.warning("job %s came back without a file", job.id)

    return await runner.video(
        replay_path, songs_dir, out_path,
        size=size, fps=fps, mute=mute, skin=skin,
        leaderboard=leaderboard, my_pictures=my_pictures, on_progress=on_progress,
    )
