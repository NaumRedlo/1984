"""Tests for the dryrun helpers — the pure functions that don't touch the DB.

The full async pipeline is exercised manually against the live database; here
we focus on the pieces where bugs would silently produce wrong numbers.
"""


from db.models.bounty import Bounty
from db.models.duel_map_pool import DuelMapPool
from scripts.dryrun_hps_recalc import _build_map_info, _pct, _quantile


class TestBuildMapInfo:
    def test_with_pool_row(self):
        bounty = Bounty(
            bounty_id="x", bounty_type="First FC", title="t",
            beatmap_id=1, beatmap_title="b", star_rating=7.0,
            drain_time=180, created_by=1, od=8.0, max_combo=2000,
        )
        pool = DuelMapPool(
            beatmap_id=1, beatmapset_id=1, title="t", artist="a", version="v",
            star_rating=7.0,
            aim_stars=9.0, speed_stars=4.0, acc_stars=6.0, cons_stars=5.0,
            w_aim=0.6, w_speed=0.1, w_acc=0.2, w_cons=0.1,
        )
        info, used_fallback = _build_map_info(bounty, pool)
        assert used_fallback is False
        assert info.aim_stars == 9.0
        assert info.speed_stars == 4.0
        assert info.w_aim == 0.6
        assert info.od == 8.0
        assert info.drain_time_seconds == 180
        assert info.max_combo == 2000

    def test_pool_missing_axis_falls_back_to_sr(self):
        # Old map rows may have NULL *_stars before the v2 backfill ran.
        bounty = Bounty(
            bounty_id="x", bounty_type="First FC", title="t",
            beatmap_id=1, beatmap_title="b", star_rating=6.5,
            drain_time=120, created_by=1, od=7.0, max_combo=1000,
        )
        pool = DuelMapPool(
            beatmap_id=1, beatmapset_id=1, title="t", artist="a", version="v",
            star_rating=6.5,
            aim_stars=None, speed_stars=None, acc_stars=None, cons_stars=None,
        )
        info, used_fallback = _build_map_info(bounty, pool)
        # used_fallback flags map-row absence, not per-axis fallback inside.
        assert used_fallback is False
        # All axes default to bounty.star_rating.
        assert info.aim_stars == info.speed_stars == info.acc_stars == info.cons_stars == 6.5

    def test_no_pool_uses_sr_uniformly(self):
        bounty = Bounty(
            bounty_id="x", bounty_type="First FC", title="t",
            beatmap_id=999, beatmap_title="b", star_rating=5.0,
            drain_time=60, created_by=1, od=5.0, max_combo=500,
        )
        info, used_fallback = _build_map_info(bounty, pool=None)
        assert used_fallback is True
        assert info.aim_stars == info.speed_stars == info.acc_stars == info.cons_stars == 5.0
        assert info.w_aim == info.w_speed == info.w_acc == info.w_cons == 0.25


class TestPct:
    def test_zero_denominator(self):
        assert _pct(0, 0) == "—"

    def test_percent_format(self):
        assert _pct(50, 100) == "50.0%"
        assert _pct(1, 3) == "33.3%"


class TestQuantile:
    def test_empty(self):
        assert _quantile([], 0.5) == 0.0

    def test_median(self):
        assert _quantile([1, 2, 3, 4, 5], 0.5) == 3

    def test_p25_p75(self):
        # 5-element list: indices 0..4; q=0.25 → idx=1, q=0.75 → idx=3
        data = [10, 20, 30, 40, 50]
        assert _quantile(data, 0.25) == 20
        assert _quantile(data, 0.75) == 40

    def test_extremes(self):
        data = [10, 20, 30, 40, 50]
        assert _quantile(data, 0.0) == 10
        assert _quantile(data, 1.0) == 50
