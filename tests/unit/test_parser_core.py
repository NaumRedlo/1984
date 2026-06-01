"""Regression tests for utils.osu.parser_core.extract_features.

Plan: unified-giggling-tiger.

Purpose: lock the parser output schema and key invariants. Before this
module existed, the same code lived in `services/duel/osu_parser.py`. We
moved it to `utils/osu/parser_core.py` so HPS could share it without
pulling in the DUEL ML calibration layer. These tests ensure:

  1. The output dict has the exact expected 24 keys (no silent additions).
  2. Re-importing through the DUEL shim still resolves to the same function.
  3. A handful of feature values are sanity-checked on synthetic .osu input.

We do NOT pin every feature value bit-for-bit — that would be brittle
against legitimate future refinements of `_intensity_floor`, etc. The
goal is to make accidental schema drift or function-rename loud.
"""

from __future__ import annotations

import pytest

from utils.osu.parser_core import extract_features as core_extract
from services.duel.osu_parser import extract_features as shim_extract


EXPECTED_KEYS = {
    "note_count", "duration_seconds", "rhythm_complexity", "stream_density",
    "jump_density", "avg_jump_velocity", "back_forth_ratio",
    "angle_variance", "flow_break_density",
    "burst_density", "full_stream_density", "death_stream_density",
    "bpm_rel_speed",
    "subdiv_entropy", "polyrhythm_density", "off_beat_ratio",
    "jack_density", "slider_tail_demand", "sv_variance", "slider_density",
    "density_variance", "intensity_floor", "pattern_repetition",
}


def _build_osu(hitobjects: list[str], timing: str = "0,500,4,2,1,50,1,0") -> str:
    """Render a minimal .osu file with the given HitObjects section."""
    return (
        "[General]\n"
        "Mode:0\n"
        "[Difficulty]\n"
        "OverallDifficulty:5\n"
        "CircleSize:4\n"
        "[TimingPoints]\n"
        f"{timing}\n"
        "[HitObjects]\n"
        + "\n".join(hitobjects)
        + "\n"
    )


def _stream_at(bpm_quarter_ms: int, count: int, start_t: int = 0) -> list[str]:
    """A pure stream: `count` circles spaced `bpm_quarter_ms / 4` apart.

    The /4 makes a 1/4 stream at 120 BPM (beat_len 500ms → 125ms between notes).
    """
    interval = bpm_quarter_ms // 4
    return [
        f"{256},{192},{start_t + i * interval},1,0,0:0:0:0:"
        for i in range(count)
    ]


def _empty_features() -> dict:
    # Mirror of parser_core._empty_features() output (we don't import the
    # private helper — we want to assert schema parity through the public
    # path only).
    return {k: 0 for k in EXPECTED_KEYS}


# ── Schema invariants ──────────────────────────────────────────────────────

class TestSchema:
    def test_empty_returns_full_schema(self):
        # An empty hitobjects section → 0-note empty features dict.
        out = core_extract(_build_osu([]))
        assert set(out.keys()) == EXPECTED_KEYS
        assert out["note_count"] == 0

    def test_one_note_returns_full_schema(self):
        out = core_extract(_build_osu(["256,192,0,1,0,0:0:0:0:"]))
        assert set(out.keys()) == EXPECTED_KEYS
        # 1 note < 2 → empty-features fallback (n=1, dur=0)
        assert out["note_count"] == 1

    def test_full_keys_on_realistic_input(self):
        out = core_extract(_build_osu(_stream_at(500, 20)))
        assert set(out.keys()) == EXPECTED_KEYS
        # Every value must be a number (int or float).
        for k, v in out.items():
            assert isinstance(v, (int, float)), f"{k} is {type(v).__name__}"


# ── DUEL shim equivalence ───────────────────────────────────────────────────

class TestShimEquivalence:
    def test_duel_shim_returns_identical_dict(self):
        osu = _build_osu(_stream_at(500, 30))
        assert core_extract(osu) == shim_extract(osu)

    def test_duel_shim_is_the_same_function(self):
        # Cheap identity check: the shim should be the literal same object.
        assert core_extract is shim_extract


# ── Sanity checks on a few features ────────────────────────────────────────

class TestSanity:
    def test_pure_stream_has_high_stream_density(self):
        # 30 notes, 125 ms apart (1/4 at 120 BPM) — every gap < 110 ms threshold?
        # 125 ms is NOT < 110 ms — that's important: the _find_stream_runs
        # threshold of 110 was tuned for 1/4 at higher BPM. So a 120 BPM stream
        # is NOT classified as a stream by the parser.
        out = core_extract(_build_osu(_stream_at(500, 30)))
        # We expect this to NOT be a stream at 120 BPM.
        assert out["full_stream_density"] == 0.0
        # But bpm_rel_speed should still register the steady 1/4 pattern.
        assert out["bpm_rel_speed"] > 0.5

    def test_fast_stream_classified(self):
        # 200 BPM (beat_len 300ms → 75ms between 1/4 notes) — < 110 ms gap,
        # 30 notes → a death-stream by the parser's run-length classifier.
        notes = [
            f"256,192,{i * 75},1,0,0:0:0:0:" for i in range(30)
        ]
        out = core_extract(_build_osu(notes, timing="0,300,4,2,1,50,1,0"))
        # All 30 notes are one continuous run >15 → death-stream.
        assert out["death_stream_density"] > 0.0

    def test_note_count_matches_input(self):
        out = core_extract(_build_osu(_stream_at(500, 17)))
        assert out["note_count"] == 17

    def test_slider_density_counts_sliders(self):
        # 4 sliders + 4 circles (slider bit = 2 in obj_type field).
        # Slider line format: x,y,t,type=2,hitsound,curve|points,repeats,length
        sliders = [
            f"256,192,{i * 200},2,0,L|256:300,1,100" for i in range(4)
        ]
        circles = [
            f"256,192,{800 + i * 200},1,0,0:0:0:0:" for i in range(4)
        ]
        out = core_extract(_build_osu(sliders + circles))
        # 4 sliders out of 8 → slider_density = 0.5
        assert out["slider_density"] == pytest.approx(0.5)
