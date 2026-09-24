from utils.ttl_cache import TTLCache


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_entries_expire():
    clock = Clock()
    cache = TTLCache(maxsize=10, ttl=5, clock=clock)
    cache["a"] = 1
    clock.now = 4.9
    assert cache.get("a") == 1 and "a" in cache
    clock.now = 5.0
    assert cache.get("a") is None and "a" not in cache and len(cache) == 0


def test_the_oldest_goes_first_past_the_limit():
    cache = TTLCache(maxsize=2, ttl=100, clock=Clock())
    cache["a"], cache["b"], cache["c"] = 1, 2, 3
    assert "a" not in cache and cache["b"] == 2 and cache["c"] == 3


def test_setting_again_renews_an_entry():
    clock = Clock()
    cache = TTLCache(maxsize=2, ttl=10, clock=clock)
    cache["a"] = 1
    cache["b"] = 2
    clock.now = 8
    cache["a"] = 1
    cache["c"] = 3
    assert "b" not in cache and "a" in cache
    clock.now = 12
    assert "a" in cache


def test_pop_and_missing_keys():
    cache = TTLCache(maxsize=2, ttl=10, clock=Clock())
    cache["a"] = None
    assert "a" in cache and cache.pop("a", "x") is None and cache.pop("a", "x") == "x"
    try:
        cache["nope"]
    except KeyError:
        pass
    else:
        raise AssertionError("a missing key must raise")


def test_expired_entries_do_not_pile_up():
    clock = Clock()
    cache = TTLCache(maxsize=1000, ttl=1, clock=clock)
    for i in range(500):
        clock.now = i * 2
        cache[i] = i
    assert len(cache._items) == 1
