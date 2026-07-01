"""Safety-net loop for the on-demand GPU render server (utils.cloud.gpu_power).

If the automatic power-off ever gives up (every retry in
gpu_power._power_off_with_retry failed) or the bot restarts while the server was
left on, nothing else would notice — the paid Intelion VM just keeps running.
This loop periodically checks for that state and recovers it.
"""

import asyncio

from config.settings import RENDER_WATCHDOG_SECONDS
from utils.cloud import gpu_power
from utils.logger import get_logger

logger = get_logger("tasks.gpu_watchdog")


async def gpu_watchdog_loop(shutdown_event: asyncio.Event) -> None:
    while not shutdown_event.is_set():
        try:
            await gpu_power.watchdog_tick()
        except Exception as e:
            logger.error(f"GPU watchdog iteration failed: {e}", exc_info=True)

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=RENDER_WATCHDOG_SECONDS)
            break
        except asyncio.TimeoutError:
            continue
