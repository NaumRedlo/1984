from datetime import datetime, timezone

from services.duel.match_monitor import (
    extract_score_stats,
    find_inprogress_game,
    find_round_score,
    game_start_time,
    mod_acronyms,
    scorev2_multiplier,
)


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


def test_mod_acronyms_handles_lazer_and_legacy_shapes():
    assert mod_acronyms({"mods": [{"acronym": "HD"}, {"acronym": "hr"}]}) == {"HD", "HR"}
    assert mod_acronyms({"mods": ["DT", "HD"]}) == {"DT", "HD"}
    assert mod_acronyms({"mods": "HD,HR"}) == {"HD", "HR"}
    assert mod_acronyms({}) == set()


def test_extract_score_stats_includes_sorted_mods():
    s = {"user_id": 1, "total_score": 700_000, "accuracy": 0.99, "passed": True,
         "max_combo": 600, "statistics": {"count_miss": 0},
         "mods": [{"acronym": "HR"}, {"acronym": "HD"}]}
    assert extract_score_stats(s)["mods"] == ["HD", "HR"]


def test_scorev2_multiplier():
    assert scorev2_multiplier([]) == 1.0
    assert scorev2_multiplier(["HR"]) == 1.10
    assert abs(scorev2_multiplier(["HD", "HR"]) - 1.06 * 1.10) < 1e-9
    assert scorev2_multiplier(["XX"]) == 1.0  # unknown mod → neutral


def test_find_inprogress_game_detects_unfinished_game_for_stall():
    # A game on our map that Bancho still reports with end_time=None — the
    # "Waiting for other players…" hang. find_inprogress_game must surface it
    # (find_round_score must NOT, since it only reads finalised games).
    payload = {
        "events": [
            {
                "game": {
                    "start_time": "2026-05-04T12:00:00Z",
                    "end_time": None,
                    "beatmap_id": 777,
                    "scores": [{"user_id": 1001, "passed": True}],
                }
            }
        ]
    }

    stuck = find_inprogress_game(payload, beatmap_id=777)
    assert stuck is not None
    assert game_start_time(stuck) == datetime(2026, 5, 4, 12, 0, tzinfo=timezone.utc)
    # A finalised result is not available yet for this hung game.
    assert find_round_score(payload, 777, 1001, 1002) is None


def test_find_inprogress_game_ignores_finished_and_other_maps():
    payload = {
        "events": [
            # finished game on our map → not a stall
            {"game": {"start_time": "2026-05-04T12:00:00Z",
                      "end_time": "2026-05-04T12:03:00Z",
                      "beatmap_id": 777, "scores": []}},
            # unfinished game, but a different map
            {"game": {"start_time": "2026-05-04T12:05:00Z",
                      "end_time": None, "beatmap_id": 888, "scores": []}},
        ]
    }
    assert find_inprogress_game(payload, beatmap_id=777) is None


def test_find_inprogress_game_respects_after_cutoff():
    # A stale unfinished game from a prior attempt (before `after`) must be
    # ignored so an abort+replay doesn't immediately re-flag the old game.
    payload = {
        "events": [
            {"game": {"start_time": "2026-05-04T12:00:00Z",
                      "end_time": None, "beatmap_id": 777, "scores": []}},
        ]
    }
    after = datetime(2026, 5, 4, 12, 1, tzinfo=timezone.utc)
    assert find_inprogress_game(payload, 777, after=after) is None
    # Returns the newest unfinished game when several are at/after the cutoff.
    payload["events"].append(
        {"game": {"start_time": "2026-05-04T12:02:00Z",
                  "end_time": None, "beatmap_id": 777, "scores": []}}
    )
    newest = find_inprogress_game(payload, 777, after=after)
    assert game_start_time(newest) == datetime(2026, 5, 4, 12, 2, tzinfo=timezone.utc)
