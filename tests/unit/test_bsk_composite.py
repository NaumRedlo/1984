from services.bsk.composite import (
    FAILED_POINTS_MULTIPLIER,
    MIN_FAILED_POINTS,
    MIN_PASSED_POINTS,
    POINTS_MULTIPLIER,
    composite_points,
    composite_score,
)


def test_passed_score_has_minimum_race_points():
    assert composite_points(10.0, 1, 1000, 100, passed=True) == MIN_PASSED_POINTS


def test_failed_score_has_lower_minimum_race_points():
    assert composite_points(10.0, 1, 1000, 100, passed=False) == MIN_FAILED_POINTS


def test_failed_score_uses_multiplier_above_floor():
    raw = composite_score(98.0, 900, 1000, 1) * POINTS_MULTIPLIER
    expected = int(raw * FAILED_POINTS_MULTIPLIER)

    assert expected > MIN_FAILED_POINTS
    assert composite_points(98.0, 900, 1000, 1, passed=False) == expected
