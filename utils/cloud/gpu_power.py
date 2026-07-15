"""On-demand power coordinator for the GPU render server.

Wraps each remote render in `session()`. Unlike a per-render wake/idle-off
cycle (which paid a cold start — RENDER_WAKE_TIMEOUT, up to a few minutes —
on every render, since a several-minute-old server had already been shut
down again), the server is kept up on a fixed schedule instead:

- The first render cold-starts the server as before AND kicks off
  `_reboot_cycle_loop`, a background task that runs forever from then on.
- Every RENDER_REBOOT_CYCLE_SECONDS, that loop waits for any in-flight
  renders to finish (never interrupts one), then stops and immediately
  restarts the server — a proactive reboot, not an idle power-off — and the
  cycle repeats. Renders that arrive while `_rebooting` is true see a
  distinct "server is restarting, please wait" message and are queued to
  resume automatically once the fresh instance is ready.
- The loop only stops if an admin (or the watchdog) explicitly powers the
  server off (`force_power_off` cancels it) or the bot process restarts
  (in-memory only, same as the rest of this module's state).

When RENDER_AUTOPOWER is off this is a transparent no-op, so the always-on
worker behaviour is unchanged.

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
    RENDER_REBOOT_CYCLE_SECONDS,
    RENDER_WAKE_TIMEOUT,
    RENDER_WORKER_URL,
)
from utils.cloud import intelion
from utils.i18n import t
from utils.logger import get_logger
from utils.osu.danser_renderer import DanserError

logger = get_logger("gpu_power")

_HEALTH_POLL_SECONDS = 2      # how often we re-check readiness during boot
_HEALTH_CHECK_TIMEOUT = 4.0   # per-request timeout for a single check
_DRAIN_POLL_SECONDS = 1        # how often the reboot loop re-checks _active during drain

_lock = asyncio.Lock()
_active = 0
_wake_task: Optional[asyncio.Task] = None

# The perpetual reboot-cycle task (started by the first render, runs until an
# admin/watchdog powers the server off). `_rebooting` is true from the moment
# a cycle boundary is hit (draining in-flight renders) through the fresh
# restart completing; `_reboot_wake_ready` lets callers that arrive during
# that window wait for the loop to create its own restart `_wake_task`
# without polling.
_reboot_task: Optional[asyncio.Task] = None
_rebooting = False
_reboot_wake_ready = asyncio.Event()

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
    button on the watchdog prompt). Also stops the perpetual reboot cycle —
    an explicit "turn it off" must actually stay off, not get powered back
    on by the cycle 45 minutes later — same retry/alert behaviour as any
    other power-off path."""
    global _reboot_task, _rebooting
    async with _lock:
        if _reboot_task is not None and not _reboot_task.done():
            _reboot_task.cancel()
        _reboot_task = None
        _rebooting = False
    return await _power_off_with_retry(context)


def snooze_watchdog(seconds: float) -> None:
    """Called from the "Оставить включённым" button — the watchdog won't
    re-check (or re-prompt) until this window passes."""
    global _watchdog_snooze_until
    _watchdog_snooze_until = time.monotonic() + seconds


async def _health_ok(timeout: float = 5.0) -> bool:
    """A 200 alone used to mean "ready" — but the worker's Python process can
    start answering /health seconds into a cold VM boot, well before Xorg's
    GPU driver stack has actually settled enough to hand out a GLX context
    (2026-07-03 incident: danser deadlocked 2.5 min after a wake despite
    /health passing almost immediately). gl_ready is the worker's own honest
    signal for that; missing (old worker not yet redeployed) defaults to True
    so this stays backward compatible."""
    url = RENDER_WORKER_URL.rstrip("/") + "/health"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.get(url) as r:
                if r.status != 200:
                    return False
                data = await r.json()
                return bool(data.get("gl_ready", True))
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


async def _wake_and_wait(on_wake: Optional[Callable[[str], Awaitable[None]]], lang: str = "en") -> None:
    if await _health_ok():
        return  # already up

    check = await intelion.get_start_check()
    if check is not None and check.get("can_start") is False:
        raise GpuPowerError(t("gpu.cannot_start", lang))

    if on_wake:
        try:
            await on_wake(t("gpu.starting", lang))
        except Exception:
            pass

    try:
        await intelion.power_on()
    except intelion.IntelionError as e:
        raise GpuPowerError(t("gpu.start_failed", lang, error=e))

    # Check right away (before the first sleep) at a tighter cadence — was
    # "sleep 5s, then check" every time, which meant up to 5s of dead time
    # before even the first look. Now the first check fires immediately, then
    # every _HEALTH_POLL_SECONDS after, so readiness is noticed as soon as
    # the next poll after the server actually answers.
    deadline = time.monotonic() + RENDER_WAKE_TIMEOUT
    while True:
        if await _health_ok(timeout=_HEALTH_CHECK_TIMEOUT):
            logger.info("GPU server is up")
            return
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(_HEALTH_POLL_SECONDS)
    raise GpuPowerError(t("gpu.wake_timeout", lang))


async def _reboot_cycle_loop() -> None:
    """Runs forever (until cancelled by `force_power_off` or the process
    restarts): every RENDER_REBOOT_CYCLE_SECONDS, drains in-flight renders
    (never interrupts one), reboots the server, then starts the timer again.
    Renders that arrive while `_rebooting` is true wait on `_reboot_wake_ready`
    for this loop's fresh `_wake_task` rather than creating their own."""
    global _wake_task, _rebooting
    try:
        while True:
            await asyncio.sleep(RENDER_REBOOT_CYCLE_SECONDS)

            async with _lock:
                _rebooting = True
                _reboot_wake_ready.clear()
            logger.info("GPU server: scheduled reboot — draining in-flight renders")
            while True:
                async with _lock:
                    if _active == 0:
                        break
                await asyncio.sleep(_DRAIN_POLL_SECONDS)

            logger.info("GPU server: scheduled reboot — stopping")
            await _power_off_with_retry("scheduled reboot")

            async with _lock:
                _wake_task = asyncio.create_task(_wake_and_wait(None))
                task = _wake_task
                _reboot_wake_ready.set()
            try:
                await task
                logger.info("GPU server: scheduled reboot — back up, cycle restarting")
            except Exception as e:
                logger.error("GPU server: scheduled reboot's restart failed: %s", e)
                await _notify_admins(
                    f"⚠️ Плановый перезапуск GPU-сервера не удался: {e}\n"
                    f"Следующий рендер попробует запустить его заново как обычно."
                )
            async with _lock:
                _rebooting = False
    except asyncio.CancelledError:
        return


async def resume_if_already_up() -> None:
    """Called once at bot startup (bot/main.py, right after `set_bot`). If
    RENDER_AUTOPOWER is on and the server turns out to already be running —
    the common case now that it stays up for a whole RENDER_REBOOT_CYCLE_SECONDS
    window, so a routine bot restart/redeploy lands mid-cycle far more often
    than not — silently re-adopt it into a fresh reboot cycle instead of
    leaving it for the watchdog to notice later and prompt an admin about
    what looks like an orphaned server but is actually completely routine.
    A genuinely orphaned server (started outside the bot's own control) is
    still caught by the watchdog as before; this only short-circuits the
    "we just don't remember starting it, but we're the ones who did" case."""
    if not RENDER_AUTOPOWER:
        return
    global _reboot_task
    if not await _health_ok():
        return  # off — nothing to resume, the next render starts it normally
    async with _lock:
        if _reboot_task is None or _reboot_task.done():
            _reboot_task = asyncio.create_task(_reboot_cycle_loop())
            logger.info("GPU server found already up at startup — resumed into the reboot cycle")


def _watchdog_prompt_kb() -> InlineKeyboardMarkup:
    # Just the two options ever actually used — "leave it on 2 hours" was
    # dropped (2026-07-15): the admin never picks it, only "off now" or a
    # 30-min snooze when there's a real reason to keep it up a bit longer.
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔴 Выключить сейчас", callback_data="gpuwd:off"),
            InlineKeyboardButton(text="🕐 Оставить на 30 мин", callback_data="gpuwd:snooze:30"),
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
    is tracking it as active or managed by the reboot cycle — e.g. the bot
    restarted mid-session and lost its in-memory state — this used to force a
    power-off outright. That once cut off a legitimate long-running render
    the accounting had lost track of, so now it only acts on real signals: it
    checks the worker's OWN in-flight counter (not just this process's
    bookkeeping) and, if that's also idle, ASKS an admin via Telegram instead
    of deciding unilaterally."""
    if not RENDER_AUTOPOWER:
        return
    async with _lock:
        reboot_cycle_active = _reboot_task is not None and not _reboot_task.done()
        if _active != 0 or _rebooting or reboot_cycle_active:
            return  # legitimately busy, mid-reboot, or the cycle has this server under control
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
async def session(on_wake: Optional[Callable[[str], Awaitable[None]]] = None, lang: str = "en"):
    """Hold the GPU server up for the duration of a render. No-op unless
    RENDER_AUTOPOWER is set. The server is never powered off just because
    renders stopped arriving — the first render starts the perpetual
    `_reboot_cycle_loop` (see module docstring), which is the only thing
    that ever stops it again, on its own fixed schedule."""
    if not RENDER_AUTOPOWER:
        yield
        return

    global _active, _wake_task, _reboot_task
    async with _lock:
        rebooting_now = _rebooting

    if rebooting_now:
        # A cycle boundary was hit — don't join `_active` yet, that would
        # block the reboot loop's drain-wait from ever reaching zero. Show
        # the caller-provided progress message ourselves (the reboot loop's
        # own internal wake has no per-caller `on_wake` to call), then wait
        # for the loop to hand us its fresh restart task.
        if on_wake:
            try:
                await on_wake(t("gpu.restarting", lang))
            except Exception:
                pass
        await _reboot_wake_ready.wait()
        async with _lock:
            _active += 1
            task = _wake_task
    else:
        async with _lock:
            was_idle = _active == 0
            _active += 1
            # When the pool was idle, (re)check readiness — _wake_and_wait health-checks
            # first, so a still-warm server returns instantly and a cold one is started.
            if was_idle:
                _wake_task = asyncio.create_task(_wake_and_wait(on_wake, lang))
                if _reboot_task is None or _reboot_task.done():
                    _reboot_task = asyncio.create_task(_reboot_cycle_loop())
            task = _wake_task

    try:
        await task  # every caller waits for the shared readiness
    except BaseException:
        async with _lock:
            _active -= 1
        raise

    try:
        yield
    finally:
        async with _lock:
            _active -= 1
