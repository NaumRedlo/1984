"""Unit tests for the BPM/length spread sampler in the DUEL map selector.

`_spread_sample` is the pure helper that keeps an auto-built pool from
clustering on one play-style: it cuts the candidates into quantile buckets on
the BPM/length fingerprint and takes one random map per bucket.
"""

from dataclasses import dataclass

from services.duel.map_selector import _spread_sample, _spread_key


@dataclass
class _Map:
    beatmap_id: int
    bpm: float = 180.0
    length: int = 120
    star_rating: float = 5.0


def test_returns_all_when_pool_smaller_than_k():
    pool = [_Map(1), _Map(2)]
    out = _spread_sample(pool, 4)
    assert {m.beatmap_id for m in out} == {1, 2}


def test_picks_exactly_k():
    pool = [_Map(i, bpm=100 + i * 10) for i in range(10)]
    out = _spread_sample(pool, 4)
    assert len(out) == 4
    assert len({m.beatmap_id for m in out}) == 4  # no duplicates


def test_spreads_across_bpm_buckets():
    # Ten maps from 100..280 bpm; asking for 2 must straddle the BPM range —
    # one from the slow half, one from the fast half (never two slow streams).
    pool = [_Map(i, bpm=100 + i * 20) for i in range(10)]  # 100,120,...,280
    for _ in range(50):
        out = _spread_sample(pool, 2)
        bpms = sorted(m.bpm for m in out)
        # buckets split 100..180 | 200..280 → always one from each half.
        assert bpms[0] <= 180 and bpms[1] >= 200


def test_zero_k_returns_empty():
    assert _spread_sample([_Map(1)], 0) == []


def test_spread_key_orders_by_bpm_then_length():
    a = _Map(1, bpm=150, length=300)
    b = _Map(2, bpm=150, length=100)
    c = _Map(3, bpm=200, length=100)
    assert _spread_key(b) < _spread_key(a) < _spread_key(c)
