import asyncio
from typing import Coroutine

from utils.logger import get_logger

logger = get_logger("utils.aio")

_background_tasks: set[asyncio.Task] = set()


def spawn(coro: Coroutine, *, name: str | None = None) -> asyncio.Task:
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
