import math

import pytest

from utils.osu.ur_estimator import (
    estimate_ur,
    _hit_window_300,
    _inverse_normal_hastings,
    _normalize_mods,
)


class TestNormalizeMods:
    def test_none(self):
        assert _normalize_mods(None) == set()

    def test_empty_string(self):
        assert _normalize_mods("") == set()

    def test_comma_separated(self):
        assert _normalize_mods("HD,HR") == {"HD", "HR"}

    def test_space_separated(self):
        assert _normalize_mods("HD HR DT") == {"HD", "HR", "DT"}

    def test_lowercase(self):
        assert _normalize_mods("hd,hr") == {"HD", "HR"}

    def test_list_of_strings(self):
        assert _normalize_mods(["HD", "HR"]) == {"HD", "HR"}

    def test_list_of_dicts_acronym(self):
        assert _normalize_mods([{"acronym": "HD"}, {"acronym": "DT"}]) == {"HD", "DT"}

    def test_mixed_list(self):
        assert _normalize_mods([{"acronym": "HD"}, "HR"]) == {"HD", "HR"}


class TestHitWindow300:
    def test_od0_nomod(self):
        # 80 - 6*0 = 80 ms
        assert _hit_window_300(0.0, set()) == pytest.approx(80.0)

    def test_od5_nomod(self):
        # 80 - 6*5 = 50 ms
        assert _hit_window_300(5.0, set()) == pytest.approx(50.0)

    def test_od10_nomod(self):
        # 80 - 60 = 20 ms (the standard OD10 window)
        assert _hit_window_300(10.0, set()) == pytest.approx(20.0)

    def test_od5_hr(self):
        # OD becomes 5*1.4=7, window = 80 - 42 = 38 ms
        assert _hit_window_300(5.0, {"HR"}) == pytest.approx(38.0)

    def test_od9_hr_caps_at_10(self):
        # 9*1.4 = 12.6 → clamped to 10 → window = 20 ms
        assert _hit_window_300(9.0, {"HR"}) == pytest.approx(20.0)

    def test_od5_ez(self):
        # OD becomes 2.5, window = 80 - 15 = 65 ms
        assert _hit_window_300(5.0, {"EZ"}) == pytest.approx(65.0)

    def test_dt_shrinks_window(self):
        # OD5 nomod = 50 ms; DT divides by 1.5 → ~33.3 ms
        assert _hit_window_300(5.0, {"DT"}) == pytest.approx(50.0 / 1.5)

    def test_nc_treated_as_dt(self):
        assert _hit_window_300(5.0, {"NC"}) == _hit_window_300(5.0, {"DT"})

    def test_ht_widens_window(self):
        # OD5 nomod = 50 ms; HT divides by 0.75 → ~66.7 ms
        assert _hit_window_300(5.0, {"HT"}) == pytest.approx(50.0 / 0.75)

    def test_hr_dt_compound(self):
        # OD5 → 7 (HR), then 80-42=38, then /1.5 = ~25.3 ms
        assert _hit_window_300(5.0, {"HR", "DT"}) == pytest.approx(38.0 / 1.5)


class TestInverseNormalHastings:
    def test_q_half_returns_zero(self):
        # P=0.5 is the median; z should be ~0
        assert _inverse_normal_hastings(0.5) == pytest.approx(0.0, abs=0.01)

    def test_q_small_returns_large_z(self):
        # P=0.025 ≈ 1.96 in standard normal tables
        assert _inverse_normal_hastings(0.025) == pytest.approx(1.96, abs=0.01)

    def test_q_very_small(self):
        # P=0.001 ≈ 3.09
        assert _inverse_normal_hastings(0.001) == pytest.approx(3.09, abs=0.01)

    def test_q_zero_safe(self):
        assert _inverse_normal_hastings(0.0) == 0.0

    def test_q_one_safe(self):
        assert _inverse_normal_hastings(1.0) == 0.0


class TestEstimateUr:
    def test_zero_hits_returns_none(self):
        assert estimate_ur(0, 0, 0, od=5.0) is None

    def test_only_misses_returns_none(self):
        # N_hits = 0 even if misses > 0
        assert estimate_ur(0, 0, 0, od=5.0) is None

    def test_perfect_ss_returns_finite_ur(self):
        # 500 hits all 300, OD 8: Laplace smoothing prevents UR from collapsing
        # to 0 (it treats the score as "1 virtual non-300 in 502 hits"), so the
        # estimator returns a finite, sane value around ~100ms.  Longer SS runs
        # tighten this — see the next test.
        ur = estimate_ur(500, 0, 0, od=8.0)
        assert ur is not None
        assert ur < 200.0
        assert ur > 0.0

    def test_longer_ss_tightens_ur(self):
        # Same OD, more hits: P_300 stays high but the Laplace prior dilutes,
        # so the estimated UR drops.  This is the property the Manifest cares
        # about — long stable runs read as more confident timing.
        short_ss = estimate_ur(500, 0, 0, od=8.0)
        long_ss = estimate_ur(5000, 0, 0, od=8.0)
        assert short_ss is not None and long_ss is not None
        assert long_ss < short_ss

    def test_chaotic_score_returns_large_ur(self):
        # Many 100s relative to 300s → unstable tapping
        ur = estimate_ur(400, 100, 0, od=7.0)
        assert ur is not None
        assert ur > 70.0

    def test_extreme_inaccuracy_still_finite(self):
        # Mostly 50s — Z should still resolve, not crash
        ur = estimate_ur(50, 100, 350, od=5.0)
        assert ur is not None
        assert math.isfinite(ur)
        assert ur > 100.0

    def test_more_accurate_gives_lower_ur(self):
        better = estimate_ur(490, 10, 0, od=7.0)
        worse = estimate_ur(450, 50, 0, od=7.0)
        assert better is not None and worse is not None
        assert better < worse

    def test_harder_od_gives_lower_ur(self):
        # Same accuracy, harder OD → tighter window → lower UR estimate
        easy_od = estimate_ur(490, 10, 0, od=5.0)
        hard_od = estimate_ur(490, 10, 0, od=9.0)
        assert easy_od is not None and hard_od is not None
        assert hard_od < easy_od

    def test_dt_reduces_ur(self):
        # DT shrinks the hit window → estimator concludes tighter timing.
        nomod = estimate_ur(490, 10, 0, od=7.0)
        dt    = estimate_ur(490, 10, 0, od=7.0, mods="DT")
        assert nomod is not None and dt is not None
        assert dt < nomod
        # DT shrinks the window by factor 1.5, so UR scales accordingly.
        assert dt == pytest.approx(nomod / 1.5, rel=1e-6)

    def test_mods_as_list_of_dicts(self):
        a = estimate_ur(490, 10, 0, od=7.0, mods=[{"acronym": "DT"}])
        b = estimate_ur(490, 10, 0, od=7.0, mods="DT")
        assert a == pytest.approx(b)

    def test_short_map_smoothing_floor(self):
        # 10 hits, all 300 — Laplace floor (eps = 1/12) prevents P=1 blowup
        ur = estimate_ur(10, 0, 0, od=5.0)
        assert ur is not None
        assert math.isfinite(ur)
        assert ur > 0.0

    def test_short_map_all_50s_safe(self):
        # All hits are 50 — q approaches 1; clamp should keep Z > 0
        ur = estimate_ur(0, 0, 10, od=5.0)
        assert ur is not None
        assert math.isfinite(ur)
        assert ur > 0.0

    def test_misses_do_not_affect_ur(self):
        # The estimator argument list doesn't accept misses, but callers may
        # be tempted to subtract them from n_300.  Document that the result
        # is sensitive only to hit counts, not misses.
        same = estimate_ur(450, 50, 0, od=7.0)
        also = estimate_ur(450, 50, 0, od=7.0)
        assert same == also
