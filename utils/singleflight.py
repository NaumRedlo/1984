import asyncio
from copy import deepcopy


class SingleFlight:
    def __init__(self):
        self._tasks = {}

    async def run(self, key, factory):
        task = self._tasks.get(key)
        if task is None:
            task = asyncio.create_task(factory())
            self._tasks[key] = task
            task.add_done_callback(lambda done: self._finished(key, done))
        return deepcopy(await asyncio.shield(task))

    def _finished(self, key, task):
        if self._tasks.get(key) is task:
            self._tasks.pop(key, None)
        if not task.cancelled():
            task.exception()

    async def close(self):
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
