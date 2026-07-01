"""On-demand power coordinator for the GPU render server.

Wraps each remote render in `session()`: the first concurrent render wakes the
Intelion server (and waits for the worker /health to come up); the last one to
finish powers it back off. A shared wake task means every caller waits for the
same readiness, and a refcount means the server is only stopped when no renders
remain in flight. When RENDER_AUTOPOWER is off this is a transparent no-op, so
the always-on worker behaviour is unchanged.

Power-off reliability (2026-07-01 incident: a single failed Intelion API call
during power-off left the server running, unnoticed, for 2+ hours — nothing
retried it): every power-off goes through `_power_off_with_retry`, which retries
before giving up and alerting an admin, and `watchdog_tick` (driven by
tasks/gpu_watchdog.py) periodically checks for a server left on with nothing
tracking it and recovers.
"""

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, Optional

import aiohttp

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config.settings import (
    ADMIN_IDS,
    RENDER_AUTOPOWER,
    RENDER_POWEROFF_RETRIES,
    RENDER_POWEROFF_RETRY_SECONDS,
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

# Set once at startup (bot/main.py) so a failed power-off can alert an admin
# from a background task, outside of any Telegram update handler.
_bot: Optional[Bot] = None

# Watchdog snooze: set by an admin tapping "leave it on" on the prompt below.
# `monotonic()` timestamp; the watchdog skips its check until this passes.
_watchdog_snooze_until: Optional[float] = None


class GpuPowerError(DanserError):
    """Wake/power failure — subclasses DanserError so the render handlers' existing
    `except DanserError` shows it as a render error."""


def set_bot(bot: Bot) -> None:
    """Register the bot instance so power-off failures can alert admins."""
    global _bot
    _bot = bot


async def _notify_admins(text: str) -> None:
    if _bot is None:
        return
    for admin_id in ADMIN_IDS:
        try:
            await _bot.send_message(admin_id, text, parse_mode="HTML")
        except Exception:
            logger.debug("failed to alert admin %s about GPU power state", admin_id)


async def _power_off_with_retry(context: str) -> bool:
    """Attempt to power off the GPU server, retrying a few times before giving
    up — a bare `except: log` here is how the server got left running for 2+
    hours unnoticed. Alerts admins only if every attempt fails (or on `raise_ok`
    to confirm a watchdog recovery), since nothing else will retry after this."""
    last_err: Optional[Exception] = None
    for attempt in range(1, RENDER_POWEROFF_RETRIES + 1):
        try:
            await intelion.power_off()
            logger.info("GPU server powered off (%s, attempt %d)", context, attempt)
            return True
        except Exception as e:
            last_err = e
            logger.error(
                "power-off attempt %d/%d failed (%s): %s",
                attempt, RENDER_POWEROFF_RETRIES, context, e,
            )
            if attempt < RENDER_POWEROFF_RETRIES:
                await asyncio.sleep(RENDER_POWEROFF_RETRY_SECONDS)
    await _notify_admins(
        f"⚠️ Не удалось выключить GPU-сервер после {RENDER_POWEROFF_RETRIES} "
        f"попыток ({context}): {last_err}\n"
        f"Сервер продолжает работать и тратить деньги — проверьте панель Intelion вручную."
    )
    return False


async def force_power_off(context: str) -> bool:
    """Public entry point for an admin-confirmed power-off (the "Выключить"
    button on the watchdog prompt) — same retry/alert behaviour as any other
    power-off path."""
    return await _power_off_with_retry(context)


def snooze_watchdog(seconds: float) -> None:
    """Called from the "Оставить включённым" button — the watchdog won't
    re-check (or re-prompt) until this window passes."""
    global _watchdog_snooze_until
    _watchdog_snooze_until = time.monotonic() + seconds


async def _health_ok(timeout: float = 5.0) -> bool:
    url = RENDER_WORKER_URL.rstrip("/") + "/health"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.get(url) as r:
                return r.status == 200
    except Exception:
        return False


async def _worker_inflight(timeout: float = 5.0) -> Optional[int]:
    """The worker's OWN render counter (danser_renderer._inflight via /health) —
    the real signal of activity, independent of this process's bookkeeping.
    None if the worker didn't answer (treated as 'can't tell' by the caller)."""
    url = RENDER_WORKER_URL.rstrip("/") + "/health"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                return int(data.get("inflight") or 0)
    except Exception:
        return None


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
    await _power_off_with_retry("warm window elapsed")


def _watchdog_prompt_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔴 Выключить сейчас", callback_data="gpuwd:off")],
        [
            InlineKeyboardButton(text="🕐 Оставить на 30 мин", callback_data="gpuwd:snooze:30"),
            InlineKeyboardButton(text="🕑 Оставить на 2 часа", callback_data="gpuwd:snooze:120"),
        ],
    ])


async def _prompt_admins_idle_server() -> None:
    if _bot is None:
        return
    text = (
        "⚠️ <b>GPU-сервер включён, но рендеров не видно</b>\n\n"
        "Ни один рендер не отслеживается как активный, а воркер не сообщает о "
        "рендерах в процессе. Обычно так бывает, если сервер забыли выключить "
        "(например, после сбоя авто-выключения) или бот перезапускался.\n\n"
        "Выключить сейчас или оставить включённым?"
    )
    for admin_id in ADMIN_IDS:
        try:
            await _bot.send_message(admin_id, text, parse_mode="HTML", reply_markup=_watchdog_prompt_kb())
        except Exception:
            logger.debug("failed to prompt admin %s about idle GPU server", admin_id)


async def watchdog_tick() -> None:
    """Periodic check (tasks/gpu_watchdog.py): if the worker is up but nothing
    is tracking it as active or scheduled to stop — e.g. every power-off retry
    already gave up, or the bot restarted mid-session and lost its in-memory
    state — this used to force a power-off outright. That once cut off a
    legitimate long-running render the accounting had lost track of, so now it
    only acts on real signals: it checks the worker's OWN in-flight counter
    (not just this process's bookkeeping) and, if that's also idle, ASKS an
    admin via Telegram instead of deciding unilaterally."""
    if not RENDER_AUTOPOWER:
        return
    async with _lock:
        if _active != 0 or _off_task is not None:
            return  # legitimately busy, or already about to stop on its own
    if not await _health_ok():
        return  # already off — nothing to recover

    if _watchdog_snooze_until is not None and time.monotonic() < _watchdog_snooze_until:
        return  # an admin already said "leave it on" recently

    inflight = await _worker_inflight()
    if inflight:
        logger.info("GPU watchdog: worker reports %d render(s) in flight — leaving it alone", inflight)
        return  # real activity on the worker itself, not just an accounting gap

    logger.warning("GPU watchdog: server is up with no tracked or reported activity — asking an admin")
    await _prompt_admins_idle_server()


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
            await _power_off_with_retry("no renders in flight")
