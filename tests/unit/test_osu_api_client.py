from utils.osu.api_client import shown_score


def test_a_play_keeps_the_score_its_player_saw_in_the_game():
    stable = {"total_score": 372289, "legacy_total_score": 21294690, "classic_total_score": 16045264}
    lazer = {"total_score": 721134, "legacy_total_score": 0, "classic_total_score": 60802468}
    bare = {"total_score": 500}
    assert shown_score(stable) == 21294690
    assert shown_score(lazer) == 60802468
    assert shown_score(bare) == 500
    assert shown_score({"total_score": 0}) == 0
    assert shown_score({}) is None
