"""Tests for the margin-aware map_type classifier.

Pool audit (May 2026) found 70% of maps had top1−top2 margin below 0.5★,
making pure argmax effectively random. These tests pin the new behaviour
introduced in `map_type_from_stars` / `map_type_from_weights`.
"""

from services.bsk.osu_parser import map_type_from_stars, map_type_from_weights


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
