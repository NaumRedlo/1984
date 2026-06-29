"""Render-worker entrypoint: `python -m services.render_worker`.

Runs ONLY the danser render HTTP service — no bot, no DB, no osu! credentials.
Deliberately does NOT call validate_settings() (the worker has no bot token).
"""

import asyncio
import signal
import sys
from contextlib import suppress

from config.settings import RENDER_WORKER_SECRET
from services.render_worker.server import RenderWorkerServer
from utils.logger import get_logger

logger = get_logger("render_worker")


async def main():
    if not RENDER_WORKER_SECRET:
        logger.error("RENDER_WORKER_SECRET is not set; refusing to start.")
        sys.exit(1)

    server = RenderWorkerServer()
    await server.start()

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(s, stop.set)

    try:
        await stop.wait()
    finally:
        await server.stop()


if __name__ == "__main__":
    asyncio.run(main())
