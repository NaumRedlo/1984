from datetime import datetime, timezone

from services.duel.match_monitor import (
    extract_score_stats,
    find_round_score,
    match_contains_users,
)


def test_match_contains_users_accepts_lobby_users_before_scores():
    payload = {
        "users": [{"id": 1001}, {"id": 1002}],
        "events": [],
    }

    assert match_contains_users(payload, 1001, 1002) is True


def test_match_contains_users_falls_back_to_completed_scores():
    payload = {
        "events": [
            {
                "game": {
                    "end_time": "2026-05-04T12:03:00Z",
                    "start_time": "2026-05-04T12:00:00Z",
                    "beatmap_id": 1,
                    "scores": [{"user_id": 1001}, {"user_id": 1002}],
                }
            }
        ]
    }

    assert match_contains_users(payload, 1001, 1002) is True


def test_failed_scores_are_extractable_round_scores():
    payload = {
        "events": [
            {
                "game": {
                    "end_time": "2026-05-04T12:03:00Z",
                    "start_time": "2026-05-04T12:00:00Z",
                    "beatmap_id": 123,
                    "scores": [
                        {
                            "user_id": 1001,
                            "score": 123456,
                            "max_combo": 111,
                            "accuracy": 0.91,
                            "passed": False,
                            "statistics": {"count_miss": 12},
                        },
                        {
                            "user_id": 1002,
                            "score": 654321,
                            "max_combo": 222,
                            "accuracy": 0.95,
                            "passed": True,
                            "statistics": {"count_miss": 3},
                        },
                    ],
                }
            }
        ]
    }

    result = find_round_score(
        payload,
        beatmap_id=123,
        p1_osu_id=1001,
        p2_osu_id=1002,
        after=datetime(2026, 5, 4, 11, 59, tzinfo=timezone.utc),
    )

    assert result is not None
    p1, p2 = result
    p1_stats = extract_score_stats(p1)
    p2_stats = extract_score_stats(p2)
    assert p1_stats["passed"] is False
    assert p2_stats["passed"] is True
    assert p1_stats["accuracy"] == 91.0
    assert p1_stats["score"] == 123456
    assert p2_stats["score"] == 654321


def test_lazer_total_score_used_when_legacy_score_is_zero():
    # osu! API lazer migration: legacy "score" is 0, value is in total_score.
    s1 = {"user_id": 1, "score": 0, "total_score": 845_000,
          "max_combo": 700, "accuracy": 0.978, "passed": True,
          "statistics": {"count_miss": 4}}
    s2 = {"user_id": 2, "score": 0, "legacy_total_score": 612_000,
          "max_combo": 510, "accuracy": 0.915, "passed": True,
          "statistics": {"count_miss": 9}}
    assert extract_score_stats(s1)["score"] == 845_000
    assert extract_score_stats(s2)["score"] == 612_000  # legacy_total_score fallback
