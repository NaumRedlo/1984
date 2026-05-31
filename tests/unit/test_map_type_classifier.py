"""Tests for the margin-aware map_type classifier.

Pool audit (May 2026) found 70% of maps had top1−top2 margin below 0.5★,
making pure argmax effectively random. These tests pin the new behaviour
introduced in `map_type_from_stars` / `map_type_from_weights`.

Pool audit-2 (May 2026, post-classifier): live pool had 48.6% mixed and
0% cons after the margin classifier, because four near-equal star values
on every dense map left no clean winner. The two-gate `classify_map_type`
(Gate-1 feature disqualifier + Gate-2 per-axis margin) restores the
specialist-axis signal. Tests for it are at the bottom of this file.
"""

from services.bsk.osu_parser import (
    classify_map_type,
    map_type_from_stars,
    map_type_from_weights,
)


# Empty-feature helper — 24 zero-valued keys + note_count/duration.
def _empty(length_s: int = 180) -> dict:
    return {
        "note_count": 1000, "duration_seconds": length_s,
        "rhythm_complexity": 0.0, "stream_density": 0.0,
        "jump_density": 0.0, "avg_jump_velocity": 0.0,
        "back_forth_ratio": 0.0, "angle_variance": 0.0,
        "flow_break_density": 0.0,
        "burst_density": 0.0, "full_stream_density": 0.0,
        "death_stream_density": 0.0, "bpm_rel_speed": 0.0,
        "subdiv_entropy": 0.0, "polyrhythm_density": 0.0,
        "off_beat_ratio": 0.0, "jack_density": 0.0,
        "slider_tail_demand": 0.0, "sv_variance": 0.0,
        "slider_density": 0.0,
        "density_variance": 0.0, "intensity_floor": 0.0,
        "pattern_repetition": 0.0,
    }


# ── map_type_from_stars ────────────────────────────────────────────────────

def test_stars_clear_winner_returns_argmax():
    stars = {"aim": 5.0, "speed": 2.0, "acc": 1.5, "cons": 1.0}
    assert map_type_from_stars(stars) == "aim"


def test_stars_tight_margin_returns_mixed():
    stars = {"aim": 3.20, "acc": 3.20, "speed": 2.59, "cons": 1.78}
    assert map_type_from_stars(stars) == "mixed"


def test_stars_margin_exactly_at_threshold_returns_argmax():
    # threshold is strictly "below" — gap == threshold counts as confident enough
    stars = {"aim": 3.0, "acc": 2.5, "speed": 1.0, "cons": 0.5}
    assert map_type_from_stars(stars, margin_threshold=0.5) == "aim"


def test_stars_margin_just_below_threshold_returns_mixed():
    stars = {"aim": 3.0, "acc": 2.51, "speed": 1.0, "cons": 0.5}
    assert map_type_from_stars(stars, margin_threshold=0.5) == "mixed"


def test_stars_zero_threshold_recovers_argmax():
    stars = {"aim": 3.20, "acc": 3.20, "speed": 2.59, "cons": 1.78}
    assert map_type_from_stars(stars, margin_threshold=0.0) in {"aim", "acc"}


def test_stars_empty_dict_returns_mixed():
    assert map_type_from_stars({}) == "mixed"


def test_stars_custom_threshold():
    stars = {"aim": 5.0, "speed": 4.5, "acc": 3.0, "cons": 1.0}
    assert map_type_from_stars(stars, margin_threshold=0.3) == "aim"
    assert map_type_from_stars(stars, margin_threshold=0.6) == "mixed"


# ── map_type_from_weights ──────────────────────────────────────────────────

def test_weights_clear_winner_returns_argmax():
    w = {"aim": 0.45, "speed": 0.20, "acc": 0.20, "cons": 0.15}
    assert map_type_from_weights(w) == "aim"


def test_weights_tight_margin_returns_mixed():
    w = {"aim": 0.30, "speed": 0.28, "acc": 0.22, "cons": 0.20}
    assert map_type_from_weights(w) == "mixed"


def test_weights_zero_threshold_recovers_argmax():
    w = {"aim": 0.30, "speed": 0.28, "acc": 0.22, "cons": 0.20}
    assert map_type_from_weights(w, margin_threshold=0.0) == "aim"


def test_weights_empty_dict_returns_mixed():
    assert map_type_from_weights({}) == "mixed"


# ── classify_map_type (two-gate) ──────────────────────────────────────────
# Gate 1: per-axis disqualifier on raw features.
#   aim   needs jump_density × avg_jump_velocity ≥ 0.12
#   speed needs full_stream_density + death_stream_density ≥ 0.20
#   acc   needs max(subdiv_entropy, polyrhythm×2, jack×2) ≥ 0.30
#   cons  needs length_s ≥ 300 AND intensity_floor ≥ 0.50
# Gate 2: argmax over qualified, with per-axis margin to the next-highest
#   star value (across ALL axes, qualified or not).


def test_classify_aim_specialist():
    feats = _empty()
    feats["jump_density"]      = 0.5
    feats["avg_jump_velocity"] = 0.6   # qualifier = 0.30 ≥ 0.12
    stars = {"aim": 5.0, "speed": 1.0, "acc": 1.0, "cons": 0.5}
    typ, conf = classify_map_type(stars, feats, length_s=120)
    assert typ == "aim"
    assert conf == "specialist"   # gap 4.0 ≥ 0.6 × 2.5


def test_classify_aim_disqualified_no_jumps():
    # Stars say "aim", but the jump-signal is below threshold.
    feats = _empty()
    feats["jump_density"]      = 0.1
    feats["avg_jump_velocity"] = 0.2   # qualifier = 0.02 < 0.12 → disqualified
    stars = {"aim": 5.0, "speed": 1.0, "acc": 1.0, "cons": 0.5}
    typ, conf = classify_map_type(stars, feats, length_s=120)
    assert typ == "mixed"
    assert conf == "mixed"


def test_classify_speed_specialist():
    feats = _empty()
    feats["full_stream_density"]  = 0.6
    feats["death_stream_density"] = 0.1   # qualifier = 0.7 ≥ 0.20
    stars = {"aim": 1.0, "speed": 6.0, "acc": 1.5, "cons": 1.0}
    typ, conf = classify_map_type(stars, feats, length_s=180)
    assert typ == "speed"
    assert conf == "specialist"


def test_classify_speed_no_streams_is_mixed():
    feats = _empty()
    feats["burst_density"] = 0.4   # bursts ≠ streams; speed needs streams
    stars = {"aim": 1.0, "speed": 4.0, "acc": 1.5, "cons": 1.0}
    typ, _ = classify_map_type(stars, feats, length_s=180)
    assert typ == "mixed"


def test_classify_acc_specialist():
    feats = _empty()
    feats["subdiv_entropy"] = 0.5   # qualifier = 0.5 ≥ 0.30
    stars = {"aim": 1.0, "speed": 1.0, "acc": 5.0, "cons": 1.0}
    typ, conf = classify_map_type(stars, feats, length_s=150)
    assert typ == "acc"
    assert conf == "specialist"


def test_classify_acc_low_features_is_mixed():
    feats = _empty()
    feats["subdiv_entropy"]      = 0.2
    feats["polyrhythm_density"]  = 0.1   # max(0.2, 0.2, 0) = 0.2 < 0.30
    stars = {"aim": 1.0, "speed": 1.0, "acc": 4.0, "cons": 1.0}
    typ, _ = classify_map_type(stars, feats, length_s=150)
    assert typ == "mixed"


def test_classify_cons_short_map_is_mixed():
    # Length gate: under 200s (_CONS_MIN_LENGTH_S), cons cannot qualify.
    feats = _empty(length_s=180)
    feats["intensity_floor"] = 0.95
    feats["density_variance"] = 0.02
    stars = {"aim": 1.0, "speed": 1.0, "acc": 1.0, "cons": 6.0}
    typ, _ = classify_map_type(stars, feats, length_s=180)
    assert typ == "mixed"


def test_classify_cons_low_composite_is_mixed():
    # Long but uneven: low floor AND high variance → composite signal below
    # threshold 0.30. (floor=0.20, variance=0.70 → composite = (0.20+0.30)/2 = 0.25)
    feats = _empty(length_s=420)
    feats["intensity_floor"] = 0.20
    feats["density_variance"] = 0.70
    stars = {"aim": 1.0, "speed": 1.0, "acc": 1.0, "cons": 5.0}
    typ, _ = classify_map_type(stars, feats, length_s=420)
    assert typ == "mixed"


def test_classify_cons_realistic_marathon_qualifies():
    # Real-pool marathon: moderate floor + low variance → composite passes.
    # floor=0.30, variance=0.15 → composite = (0.30 + 0.85)/2 = 0.575 ≥ 0.30
    feats = _empty(length_s=480)
    feats["intensity_floor"] = 0.30
    feats["density_variance"] = 0.15
    stars = {"aim": 1.0, "speed": 2.0, "acc": 1.5, "cons": 5.5}
    typ, conf = classify_map_type(stars, feats, length_s=480)
    assert typ == "cons"
    assert conf in {"specialist", "leaning"}


def test_classify_cons_specialist_marathon():
    feats = _empty(length_s=600)
    feats["intensity_floor"]  = 0.85
    feats["density_variance"] = 0.05
    stars = {"aim": 1.0, "speed": 2.0, "acc": 1.5, "cons": 9.0}
    typ, conf = classify_map_type(stars, feats, length_s=600)
    assert typ == "cons"
    assert conf == "specialist"


def test_classify_confidence_levels():
    # Margins are RATIOS (gap / top_star), not absolute stars.
    # aim ratio = 0.15 → mixed if (top-runner)/top < 0.15.
    #               leaning if 0.15 ≤ ratio < 0.30.
    #               specialist if ratio ≥ 0.30.
    feats = _empty()
    feats["jump_density"] = 0.5
    feats["avg_jump_velocity"] = 0.6

    # Tight: 3.0 vs 2.7 → ratio = 0.10 < 0.15 → mixed.
    stars_tight = {"aim": 3.0, "speed": 2.7, "acc": 1.0, "cons": 1.0}
    typ, conf = classify_map_type(stars_tight, feats, length_s=120)
    assert typ == "mixed" and conf == "mixed"

    # Leaning: 3.0 vs 2.4 → ratio = 0.20, in [0.15, 0.30) → leaning.
    stars_lean = {"aim": 3.0, "speed": 2.4, "acc": 1.0, "cons": 1.0}
    typ, conf = classify_map_type(stars_lean, feats, length_s=120)
    assert typ == "aim" and conf == "leaning"

    # Specialist: 5.0 vs 2.0 → ratio = 0.60 ≥ 0.30 → specialist.
    stars_spec = {"aim": 5.0, "speed": 2.0, "acc": 1.0, "cons": 1.0}
    typ, conf = classify_map_type(stars_spec, feats, length_s=120)
    assert typ == "aim" and conf == "specialist"


def test_classify_relative_margin_scales_with_sr():
    # Same 20% gap → leaning at any star level.
    feats = _empty()
    feats["jump_density"] = 0.5
    feats["avg_jump_velocity"] = 0.6

    # 3★ leader, 2.4★ runner (20% gap)
    typ_low, conf_low = classify_map_type(
        {"aim": 3.0, "speed": 2.4, "acc": 1.0, "cons": 1.0}, feats, length_s=120,
    )
    # 8★ leader, 6.4★ runner (20% gap)
    typ_high, conf_high = classify_map_type(
        {"aim": 8.0, "speed": 6.4, "acc": 1.0, "cons": 1.0}, feats, length_s=120,
    )
    # Both should produce identical labels — that's the point of ratios.
    assert typ_low == typ_high == "aim"
    assert conf_low == conf_high == "leaning"


def test_classify_no_qualified_axes_returns_mixed():
    # Empty features dict: nothing qualifies, regardless of stars.
    feats = _empty()
    stars = {"aim": 2.0, "speed": 2.0, "acc": 2.0, "cons": 2.0}
    typ, conf = classify_map_type(stars, feats, length_s=180)
    assert typ == "mixed"
    assert conf == "mixed"


def test_classify_disqualified_axis_still_competes_for_runner_up():
    # An axis can be disqualified (gate-1 fails) yet still pull a borderline
    # winner into 'mixed' because its star value counts as competing signal.
    feats = _empty()
    feats["jump_density"]      = 0.5   # aim qualifies
    feats["avg_jump_velocity"] = 0.6
    # speed has no stream-signal → disqualified, but its stars are close.
    stars = {"aim": 3.0, "speed": 2.7, "acc": 1.0, "cons": 1.0}
    typ, _ = classify_map_type(stars, feats, length_s=120)
    # aim wins gate-1 alone but gap to speed = 0.3 < 0.6 margin → mixed
    assert typ == "mixed"


def test_classify_empty_stars_returns_mixed():
    typ, conf = classify_map_type({}, _empty(), length_s=180)
    assert typ == "mixed" and conf == "mixed"
