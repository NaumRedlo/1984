"""Map-leaderboard ranking metric by beatmap status.

LOVED / unranked maps award no pp, so their map leaderboard ranks by total
score instead of pp (which would be all-zeros). Ranked/approved keep pp. This
locks the status→metric rule (`_ranks_by_score`), tolerant of both the string
and integer status shapes the osu! API returns.
"""

import pytest

from services.leaderboard.service import _ranks_by_score


@pytest.mark.parametrize("status", ["loved", "LOVED", "qualified", "pending", "wip", "graveyard"])
def test_score_ranked_statuses(status):
    assert _ranks_by_score(status) is True


@pytest.mark.parametrize("status", ["ranked", "approved", "RANKED"])
def test_pp_ranked_statuses(status):
    assert _ranks_by_score(status) is False


@pytest.mark.parametrize("status,expected", [
    (4, True),    # loved
    (3, True),    # qualified
    (2, False),   # approved
    (1, False),   # ranked
    (0, True),    # pending
    (-1, True),   # wip
    (-2, True),   # graveyard
])
def test_integer_status_forms(status, expected):
    assert _ranks_by_score(status) is expected


@pytest.mark.parametrize("status", ["", None, "unknown_future_status"])
def test_unknown_status_falls_back_to_pp(status):
    # When status is missing/blank (e.g. the beatmap fetch failed) we keep the
    # old pp behaviour rather than silently switching to score.
    assert _ranks_by_score(status) is False
