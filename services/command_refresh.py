import math
import time

from utils.singleflight import SingleFlight
from utils.ttl_cache import TTLCache


class RefreshCooldown(Exception):
    def __init__(self, seconds):
        self.seconds = max(1, math.ceil(seconds))


class RefreshBusy(Exception):
    pass


class RefreshRequests:
    def __init__(self):
        self._flight = SingleFlight()
        self._finished = TTLCache(maxsize=20000, ttl=30, clock=lambda: time.monotonic())
        self._active = {}

    async def run(self, key, factory, *, scope=None):
        scope = key if scope is None else scope
        if scope in self._active and self._active[scope] != key:
            raise RefreshBusy()
        finished = self._finished.get(scope)
        if finished is not None:
            raise RefreshCooldown(30 - (time.monotonic() - finished))

        async def refresh():
            try:
                result = await factory()
                if result is not None and result is not False:
                    self._finished[scope] = time.monotonic()
                return result
            finally:
                self._active.pop(scope, None)

        self._active[scope] = key
        return await self._flight.run(key, refresh)


requests = RefreshRequests()
