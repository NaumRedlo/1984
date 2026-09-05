import pytest

from services.leaderboard.service import _ranks_by_score

@pytest.mark.parametrize("status", ["loved", "LOVED", "qualified", "pending", "wip", "graveyard"])
def test_score_ranked_statuses(status):
    assert _ranks_by_score(status) is True

@pytest.mark.parametrize("status", ["ranked", "approved", "RANKED"])
def test_pp_ranked_statuses(status):
    assert _ranks_by_score(status) is False

@pytest.mark.parametrize("status,expected", [
    (4, True),
    (3, True),
    (2, False),
    (1, False),
    (0, True),
    (-1, True),
    (-2, True),
])
def test_integer_status_forms(status, expected):
    assert _ranks_by_score(status) is expected

@pytest.mark.parametrize("status", ["", None, "unknown_future_status"])
def test_unknown_status_falls_back_to_pp(status):
    assert _ranks_by_score(status) is False
