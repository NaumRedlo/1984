"""Unit tests for services.hps.hps_profile.

Plan: unified-giggling-tiger.

Cover the public output schema + bucket boundaries + a few sanity checks
on the typing_hints rules. Most rules are intentionally simple linear
expressions over features — we don't pin exact numeric outputs, only the
inequalities that justify each rule's existence.
"""

from __future__ import annotations

import pytest

from services.hps.hps_profile import (
    compute_hps_profile,
    _length_bucket,
    _bpm_bucket,
    _genre_tag,
)


# ── Length / BPM buckets ────────────────────────────────────────────────────

class TestLengthBucket:
    @pytest.mark.parametrize("seconds,expected", [
        (0,    "short"),
        (60,   "short"),
        (120,  "short"),
        (121,  "medium"),
        (300,  "medium"),
        (301,  "long"),
        (600,  "long"),
        (601,  "marathon"),
        (1200, "marathon"),
    ])
    def test_boundaries(self, seconds, expected):
        assert _length_bucket(seconds) == expected


class TestBpmBucket:
    @pytest.mark.parametrize("bpm,expected", [
        (0,    "mid"),   # unknown
        (100,  "slow"),
        (149,  "slow"),
        (150,  "mid"),
        (199,  "mid"),
        (200,  "fast"),
        (249,  "fast"),
        (250,  "speedcore"),
        (320,  "speedcore"),
    ])
    def test_boundaries(self, bpm, expected):
        assert _bpm_bucket(bpm) == expected


# ── Genre tag ───────────────────────────────────────────────────────────────

class TestGenreTag:
    def test_stream_dominant(self):
        feat = {
            "full_stream_density": 0.5, "death_stream_density": 0.2,
            "jump_density": 0.05, "avg_jump_velocity": 0.1,
            "subdiv_entropy": 0.1, "polyrhythm_density": 0.05,
        }
        assert _genre_tag(feat) == "stream"

    def test_jump_dominant(self):
        feat = {
            "full_stream_density": 0.05, "death_stream_density": 0.0,
            "jump_density": 0.4, "avg_jump_velocity": 0.8,
            "subdiv_entropy": 0.1, "polyrhythm_density": 0.05,
        }
        assert _genre_tag(feat) == "jump"

    def test_tech_dominant(self):
        feat = {
            "full_stream_density": 0.05, "death_stream_density": 0.0,
            "jump_density": 0.05, "avg_jump_velocity": 0.1,
            "subdiv_entropy": 0.6, "polyrhythm_density": 0.3,
        }
        assert _genre_tag(feat) == "tech"

    def test_mixed_when_weak(self):
        # All signals below the 0.15 floor → mixed.
        feat = {
            "full_stream_density": 0.05, "death_stream_density": 0.05,
            "jump_density": 0.02, "avg_jump_velocity": 0.1,
            "subdiv_entropy": 0.05, "polyrhythm_density": 0.05,
        }
        assert _genre_tag(feat) == "mixed"

    def test_mixed_when_close(self):
        # Top and runner-up within 0.05 → mixed (margin guard).
        feat = {
            "full_stream_density": 0.30, "death_stream_density": 0.0,  # stream=0.30
            "jump_density": 0.16, "avg_jump_velocity": 0.85,            # jump≈0.296
            "subdiv_entropy": 0.05, "polyrhythm_density": 0.05,
        }
        assert _genre_tag(feat) == "mixed"


# ── Public API: schema + None input ─────────────────────────────────────────

EXPECTED_TYPES = {"Marathon", "SS", "Accuracy", "Metronome", "Mod", "Pass", "First FC"}


class TestPublicAPI:
    def test_none_osu_text_still_returns_full_schema(self):
        out = compute_hps_profile(
            None,
            bpm=180, ar=9, od=8, length_s=200, star_rating=5.0,
            ranked_status="ranked",
        )
        assert set(out.keys()) == {
            "features", "genre_tag", "length_bucket",
            "bpm_bucket", "ranked_status", "typing_hints",
        }
        assert set(out["typing_hints"].keys()) == EXPECTED_TYPES
        for v in out["typing_hints"].values():
            assert 0.0 <= v <= 1.0

    def test_ranked_status_passthrough(self):
        for status in ("ranked", "loved", "qualified"):
            out = compute_hps_profile(
                None, bpm=180, ar=9, od=8, length_s=200,
                star_rating=5.0, ranked_status=status,
            )
            assert out["ranked_status"] == status

    def test_buckets_derived_from_metadata(self):
        out = compute_hps_profile(
            None, bpm=220, ar=9, od=8, length_s=700, star_rating=7.0,
        )
        assert out["length_bucket"] == "marathon"
        assert out["bpm_bucket"]    == "fast"


# ── Typing hints — sanity checks per rule ───────────────────────────────────

def _hints(**kwargs):
    """Convenience: build a profile with minimal kwargs and return hints."""
    defaults = dict(
        bpm=180, ar=9, od=8, length_s=240, star_rating=5.0, ranked_status="ranked",
    )
    defaults.update(kwargs)
    return compute_hps_profile(None, **defaults)["typing_hints"]


class TestMarathonHint:
    def test_above_10min_full_score(self):
        assert _hints(length_s=700)["Marathon"] == 1.0

    def test_below_5min_zero(self):
        assert _hints(length_s=200)["Marathon"] == 0.0

    def test_ramp_between_5_and_10_min(self):
        # 300..600 → 0..1, so 450s ≈ 0.5
        h = _hints(length_s=450)["Marathon"]
        assert 0.3 < h < 0.7


class TestSsHint:
    def test_too_long_zero(self):
        assert _hints(length_s=400)["SS"] == 0.0

    def test_high_od_short_map_positive(self):
        assert _hints(length_s=180, od=10)["SS"] > 0.5

    def test_low_od_lower_score(self):
        hi = _hints(length_s=180, od=10)["SS"]
        lo = _hints(length_s=180, od=4)["SS"]
        assert hi > lo


class TestAccuracyHint:
    def test_increases_with_od(self):
        lo = _hints(od=4)["Accuracy"]
        hi = _hints(od=9)["Accuracy"]
        assert hi > lo


class TestModHint:
    def test_low_sr_high_score(self):
        # Mod-warmups: SR < 4.5 → sr_factor = 1.0
        assert _hints(star_rating=3.0)["Mod"] > 0.7

    def test_high_sr_zero(self):
        # SR ≥ 6.0 → sr_factor = 0.0; only the 0.3*simple residual remains.
        assert _hints(star_rating=7.5)["Mod"] <= 0.3

    def test_unknown_sr_neutral(self):
        # SR=0 → neutral 0.5
        assert _hints(star_rating=0)["Mod"] == 0.5


class TestPassHint:
    def test_low_sr_zero(self):
        # SR < 5 → sr_factor = 0; only the 0.3*len_factor residual remains.
        assert _hints(star_rating=3.0, length_s=300)["Pass"] <= 0.3

    def test_high_sr_positive(self):
        assert _hints(star_rating=8.0, length_s=300)["Pass"] > 0.5

    def test_marathon_range_downweighted(self):
        # Marathon range gets len_factor = 0.3 (low) — the Marathon bounty
        # takes priority for those maps.
        v_normal = _hints(star_rating=8.0, length_s=300)["Pass"]
        v_marathon = _hints(star_rating=8.0, length_s=700)["Pass"]
        assert v_normal > v_marathon


class TestFirstFcHint:
    def test_neutral_baseline(self):
        # First FC is the fallback — always positive everywhere.
        for length_s in (60, 240, 700):
            assert _hints(length_s=length_s)["First FC"] > 0
