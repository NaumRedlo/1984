"""Replay-upload anti-forgery: the uploaded .osr must match a real osu! score.

Covers the fingerprint match (lazer + legacy stat keys) and the lookup helper
that rejects a play never submitted online. Pure/duck-typed — no Telegram, no
network (a fake api client stands in).
"""

import asyncio
from types import SimpleNamespace

from bot.handlers.bounty.replay import (
    _find_matching_real_score,
    _fingerprint_matches,
)


def _replay(n300=500, n100=3, n50=0, miss=1, combo=600):
    return SimpleNamespace(
        count_300=n300, count_100=n100, count_50=n50, count_miss=miss,
        max_combo=combo,
    )


def _score(great=500, ok=3, meh=0, miss=1, combo=600, **extra):
    s = {"statistics": {"great": great, "ok": ok, "meh": meh, "miss": miss},
         "max_combo": combo}
    s.update(extra)
    return s


class _FakeApi:
    def __init__(self, scores):
        self._s = scores

    async def get_user_beatmap_scores(self, beatmap_id, user_id, oauth_token=None):
        return self._s


def test_fingerprint_matches_lazer_keys():
    assert _fingerprint_matches(_replay(), _score()) is True
    assert _fingerprint_matches(_replay(combo=600), _score(combo=599)) is False
    assert _fingerprint_matches(_replay(miss=1), _score(miss=2)) is False


def test_fingerprint_matches_legacy_keys():
    legacy = {"statistics": {"count_300": 500, "count_100": 3, "count_50": 0,
                             "count_miss": 1}, "max_combo": 600}
    assert _fingerprint_matches(_replay(), legacy) is True


def test_find_matching_real_score_found_and_missing():
    bounty = SimpleNamespace(beatmap_id=10)
    r = _replay()
    hit = _FakeApi([_score(combo=400), _score()])      # 2nd entry matches
    miss = _FakeApi([_score(combo=400), _score(miss=5)])  # none match
    assert asyncio.run(_find_matching_real_score(r, bounty, 123, hit, None)) is not None
    assert asyncio.run(_find_matching_real_score(r, bounty, 123, miss, None)) is None


def test_find_matching_real_score_api_error_returns_none():
    class _Boom:
        async def get_user_beatmap_scores(self, *a, **k):
            raise RuntimeError("boom")

    bounty = SimpleNamespace(beatmap_id=10)
    assert asyncio.run(
        _find_matching_real_score(_replay(), bounty, 1, _Boom(), None)
    ) is None
