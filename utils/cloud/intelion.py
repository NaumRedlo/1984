"""Intelion Cloud server power API (https://intelion.cloud/api/v2).

Used for on-demand GPU rendering: power the render server on before a render and
off afterwards (Intelion bills per-second; a stopped server is free). Auth is a
per-account API token sent as `Authorization: Token <key>` — kept in .env, never
committed.
"""

from typing import Optional

import aiohttp

from config.settings import INTELION_API_URL, INTELION_API_TOKEN, INTELION_SERVER_ID
from utils.logger import get_logger

logger = get_logger("intelion")

# Server status codes from the actions endpoint.
STATUS_ACTIVE = 2    # run
STATUS_PAUSED = -1   # stop (billing halts)


class IntelionError(Exception):
    """Raised when an Intelion API call fails."""


def _headers() -> dict:
    return {"Authorization": f"Token {INTELION_API_TOKEN}"}


def _server_url(suffix: str) -> str:
    base = INTELION_API_URL.rstrip("/")
    return f"{base}/cloud-servers/{INTELION_SERVER_ID}/{suffix}"


async def _action(status_value) -> None:
    """POST a power action to the server (start/stop/reboot)."""
    if not INTELION_API_TOKEN or not INTELION_SERVER_ID:
        raise IntelionError("INTELION_API_TOKEN / INTELION_SERVER_ID не заданы.")
    url = _server_url("actions/")
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json={"status": status_value}, headers=_headers()) as resp:
            if resp.status >= 400:
                body = (await resp.text())[:200]
                raise IntelionError(f"actions {status_value} -> HTTP {resp.status}: {body}")
            logger.info("intelion action status=%s -> HTTP %s", status_value, resp.status)


async def power_on() -> None:
    await _action(STATUS_ACTIVE)


async def power_off() -> None:
    await _action(STATUS_PAUSED)


async def get_start_check() -> Optional[dict]:
    """GET the pre-start check (can_start, balance, affordable_runtime_seconds).
    Best-effort: returns None on any failure so a flaky check never blocks a
    render (a real problem will surface when power_on is attempted)."""
    if not INTELION_API_TOKEN or not INTELION_SERVER_ID:
        return None
    url = _server_url("status/")
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=_headers()) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
    except Exception as e:
        logger.debug("intelion start-check failed: %s", e)
        return None
