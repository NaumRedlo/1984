"""On-demand power coordinator for the GPU render server.

Wraps each remote render in `session()`: the first concurrent render wakes the
Intelion server (and waits for the worker /health to come up); the last one to
finish powers it back off. A shared wake task means every caller waits for the
same readiness, and a refcount means the server is only stopped when no renders
remain in flight. When RENDER_AUTOPOWER is off this is a transparent no-op, so
the always-on worker behaviour is unchanged.
"""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, Optional

import aiohttp

from config.settings import (
    RENDER_AUTOPOWER,
    RENDER_WAKE_TIMEOUT,
    RENDER_WARM_SECONDS,
    RENDER_WORKER_URL,
)
from utils.cloud import intelion
from utils.logger import get_logger
from utils.osu.danser_renderer import DanserError

logger = get_logger("gpu_power")

_HEALTH_POLL_SECONDS = 5

_lock = asyncio.Lock()
_active = 0
_wake_task: Optional[asyncio.Task] = None
# Pending delayed power-off (the warm window). Cancelled when a new render arrives.
_off_task: Optional[asyncio.Task] = None


class GpuPowerError(DanserError):
    """Wake/power failure — subclasses DanserError so the render handlers' existing
    `except DanserError` shows it as a render error."""


async def _health_ok(timeout: float = 5.0) -> bool:
    url = RENDER_WORKER_URL.rstrip("/") + "/health"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.get(url) as r:
                return r.status == 200
    except Exception:
        return False


async def _wake_and_wait(on_wake: Optional[Callable[[str], Awaitable[None]]]) -> None:
    if await _health_ok():
        return  # already up

    check = await intelion.get_start_check()
    if check is not None and check.get("can_start") is False:
        raise GpuPowerError("GPU-сервер нельзя запустить — проверьте баланс Intelion.")

    if on_wake:
        try:
            await on_wake("Запускаю GPU-сервер (~1 мин)...")
        except Exception:
            pass

    try:
        await intelion.power_on()
    except intelion.IntelionError as e:
        raise GpuPowerError(f"Не удалось запустить GPU-сервер: {e}")

    deadline = time.monotonic() + RENDER_WAKE_TIMEOUT
    while time.monotonic() < deadline:
        await asyncio.sleep(_HEALTH_POLL_SECONDS)
        if await _health_ok():
            logger.info("GPU server is up")
            return
    raise GpuPowerError("GPU-сервер не успел запуститься. Попробуйте ещё раз.")


async def _delayed_power_off() -> None:
    """Power the server off after the warm window, unless a new render arrived."""
    try:
        await asyncio.sleep(RENDER_WARM_SECONDS)
    except asyncio.CancelledError:
        return
    global _wake_task, _off_task
    async with _lock:
        if _active != 0:
            return  # a render is using it again
        _wake_task = None
        _off_task = None
    try:
        await intelion.power_off()
        logger.info("GPU server powered off after warm window")
    except Exception as e:
        logger.error("failed to power off GPU server: %s", e)


@asynccontextmanager
async def session(on_wake: Optional[Callable[[str], Awaitable[None]]] = None):
    """Hold the GPU server up for the duration of a render. No-op unless
    RENDER_AUTOPOWER is set. After the last render the server is kept warm for
    RENDER_WARM_SECONDS before powering off (cancelled if a new render arrives)."""
    if not RENDER_AUTOPOWER:
        yield
        return

    global _active, _wake_task, _off_task
    async with _lock:
        # Cancel a pending power-off — we're using the server again.
        if _off_task is not None and not _off_task.done():
            _off_task.cancel()
        _off_task = None
        was_idle = _active == 0
        _active += 1
        # When the pool was idle, (re)check readiness — _wake_and_wait health-checks
        # first, so a still-warm server returns instantly and a cold one is started.
        if was_idle:
            _wake_task = asyncio.create_task(_wake_and_wait(on_wake))
        task = _wake_task

    try:
        await task  # every caller waits for the shared readiness
    except BaseException:
        async with _lock:
            _active -= 1
            if _active == 0:
                _wake_task = None
        raise

    try:
        yield
    finally:
        async with _lock:
            _active -= 1
            start_warmdown = _active == 0
            if start_warmdown and RENDER_WARM_SECONDS > 0:
                _off_task = asyncio.create_task(_delayed_power_off())
                start_warmdown = False  # handled by the delayed task
        if start_warmdown:
            # Immediate power-off (warm window disabled).
            _wake_task = None
            try:
                await intelion.power_off()
                logger.info("GPU server powered off (no renders in flight)")
            except Exception as e:
                logger.error("failed to power off GPU server: %s", e)
