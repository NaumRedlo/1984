import time
from collections import OrderedDict
from typing import Any, Callable, Hashable, Optional


class TTLCache:
    """A dict that forgets: entries expire after `ttl` seconds and the oldest go first past `maxsize`."""

    def __init__(self, maxsize: int, ttl: float, *, clock: Callable[[], float] = time.monotonic):
        self.maxsize = maxsize
        self.ttl = ttl
        self._clock = clock
        self._items: "OrderedDict[Hashable, tuple[float, Any]]" = OrderedDict()

    def get(self, key: Hashable, default: Optional[Any] = None) -> Any:
        kept = self._items.get(key)
        if kept is None:
            return default
        if self._clock() - kept[0] >= self.ttl:
            del self._items[key]
            return default
        return kept[1]

    def __setitem__(self, key: Hashable, value: Any) -> None:
        now = self._clock()
        self._items.pop(key, None)
        self._items[key] = (now, value)
        self._trim(now)

    def __getitem__(self, key: Hashable) -> Any:
        marker = object()
        value = self.get(key, marker)
        if value is marker:
            raise KeyError(key)
        return value

    def __contains__(self, key: Hashable) -> bool:
        marker = object()
        return self.get(key, marker) is not marker

    def pop(self, key: Hashable, default: Optional[Any] = None) -> Any:
        marker = object()
        value = self.get(key, marker)
        self._items.pop(key, None)
        return default if value is marker else value

    def clear(self) -> None:
        self._items.clear()

    def __len__(self) -> int:
        self._trim(self._clock())
        return len(self._items)

    def _trim(self, now: float) -> None:
        while self._items:
            key, (stamp, _) = next(iter(self._items.items()))
            if now - stamp >= self.ttl or len(self._items) > self.maxsize:
                del self._items[key]
            else:
                break
