"""asyncio helpers shared across the bot."""

import asyncio
from typing import Coroutine

from utils.logger import get_logger

logger = get_logger("utils.aio")

# asyncio keeps only a *weak* reference to bare tasks created by create_task, so
# a fire-and-forget task can be garbage-collected mid-flight and vanish without
# a trace.  Holding a strong reference here until the task finishes prevents
# that.  See https://docs.python.org/3/library/asyncio-task.html#asyncio.create_task
_background_tasks: set[asyncio.Task] = set()


def spawn(coro: Coroutine, *, name: str | None = None) -> asyncio.Task:
    """Schedule a fire-and-forget coroutine that can never die silently.

    Unlike a bare ``asyncio.create_task``, this logs any unhandled exception
    (with traceback) instead of dropping it on the floor, and keeps a strong
    reference to the task so it isn't garbage-collected before it completes.
    """
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)

    def _on_done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            logger.error(
                f"background task {t.get_name()!r} crashed: {exc}",
                exc_info=exc,
            )

    task.add_done_callback(_on_done)
    return task
