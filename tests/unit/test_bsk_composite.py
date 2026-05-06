from services.bsk.composite import (
    FAILED_POINTS_MULTIPLIER,
    POINTS_MULTIPLIER,
    composite_points,
    composite_score,
)


def test_passed_score_scales_with_quality():
    raw = composite_score(10.0, 1, 1000, 100) * POINTS_MULTIPLIER
    assert composite_points(10.0, 1, 1000, 100, passed=True) == int(raw)


def test_failed_score_applies_multiplier():
    raw = composite_score(10.0, 1, 1000, 100) * POINTS_MULTIPLIER
    expected = int(raw * FAILED_POINTS_MULTIPLIER)
    assert composite_points(10.0, 1, 1000, 100, passed=False) == expected


def test_failed_score_uses_multiplier_above_min():
    raw = composite_score(98.0, 900, 1000, 1) * POINTS_MULTIPLIER
    expected = int(raw * FAILED_POINTS_MULTIPLIER)
    assert composite_points(98.0, 900, 1000, 1, passed=False) == expected


def test_high_quality_passed_score_dominates_failed_score():
    high = composite_points(99.0, 990, 1000, 0, passed=True)
    low = composite_points(60.0, 200, 1000, 80, passed=True)
    assert high > 5 * low
