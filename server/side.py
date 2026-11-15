import asyncio
from contextlib import suppress
from typing import Optional

from services.oauth.server import OAuthServer, set_linked
from services.render_farm import http as farm_http
from services.render_farm.roster import roster
from tasks.duels import run as run_duels
from tasks.live_tracker import LiveTracker, set_current as set_live_tracker
from tasks.map_checks import run as check_maps
from tasks.profile_updater import periodic_profile_updates
from utils.logger import get_logger

logger = get_logger("server.side")


class AppSide:
    def __init__(self, osu, shutdown: asyncio.Event, port: Optional[int] = None) -> None:
        self.osu = osu
        self.port = port
        self.shutdown = shutdown
        self.http: Optional[OAuthServer] = None
        self.tasks: list[asyncio.Task] = []

    async def start(self, telegram, linked, duel_bot=None) -> None:
        logger.info("Starting the HTTP side...")
        self.http = OAuthServer() if self.port is None else OAuthServer(self.port)
        await self.http.start()
        farm_http.set_telegram(telegram)
        set_linked(linked)
        farm_http.set_osu(self.osu)

        tracker = LiveTracker(self.osu)
        set_live_tracker(tracker)
        logger.info("Starting the loops: workers told, profile updater, live tracker, map checks, duels...")
        self.tasks = [
            asyncio.create_task(roster.tell(self.shutdown), name="render_roster"),
            asyncio.create_task(periodic_profile_updates(self.osu, self.shutdown), name="profile_updater"),
            asyncio.create_task(tracker.run(self.shutdown), name="live_tracker"),
            asyncio.create_task(check_maps(self.osu, self.shutdown), name="map_checks"),
            asyncio.create_task(run_duels(self.shutdown, self.osu, duel_bot), name="duels"),
        ]

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
        self.tasks = []
        set_live_tracker(None)
        farm_http.set_telegram(None)
        set_linked(None)
        if self.http is not None:
            await self.http.stop()
            self.http = None
