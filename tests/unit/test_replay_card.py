"""A judged replay, in the shape the result card is drawn from.

The card `rs` sends is built from a raw osu! API score. A replay is the same
event arriving by a different road, so it gets the same card rather than a
second layout to drift — which means turning one into the other, and the two
things a replay does not carry are worked out here: the rank letter and the pp.
"""

import pytest

from services.dossier.card import _mod_list, rank_of, score_from_replay


class TestTheRankLetter:
    """osu! states its ladder in shares of the object count, not in accuracy."""

    @pytest.mark.parametrize(
        "counts, mods, want",
        [
            ({"300": 100, "100": 0, "50": 0, "miss": 0}, [], "X"),
            ({"300": 100, "100": 0, "50": 0, "miss": 0}, ["HD"], "XH"),
            ({"300": 100, "100": 0, "50": 0, "miss": 0}, ["FL"], "XH"),
            ({"300": 95, "100": 5, "50": 0, "miss": 0}, [], "S"),
            ({"300": 95, "100": 5, "50": 0, "miss": 0}, ["HD"], "SH"),
            # Over 90% 300s but one miss: S is out, A stands.
            ({"300": 95, "100": 3, "50": 0, "miss": 2}, [], "A"),
            ({"300": 85, "100": 15, "50": 0, "miss": 0}, [], "A"),
            ({"300": 75, "100": 25, "50": 0, "miss": 0}, [], "B"),
            ({"300": 65, "100": 30, "50": 5, "miss": 0}, [], "C"),
            ({"300": 50, "100": 30, "50": 10, "miss": 10}, [], "D"),
        ],
    )
    def test_the_ladder(self, counts, mods, want):
        assert rank_of(counts, mods) == want

    def test_fifty_percent_of_fifties_costs_the_s(self):
        """The rule nobody remembers: an S needs under 1% of 50s, so a play
        that is otherwise clean loses it on those alone."""
        clean = {"300": 950, "100": 40, "50": 10, "miss": 0}
        assert rank_of(clean, []) == "A"
        assert rank_of({**clean, "100": 50, "50": 0}, []) == "S"

    def test_a_play_with_nothing_in_it_is_not_a_rank(self):
        assert rank_of({"300": 0, "100": 0, "50": 0, "miss": 0}, []) == "F"


class TestTheModString:
    def test_acronyms_are_two_characters_each(self):
        assert _mod_list("NFHDV2") == ["NF", "HD", "V2"]

    @pytest.mark.parametrize("nothing", ["", "NM", "None"])
    def test_no_mods_is_an_empty_list_not_a_mod_called_nm(self, nothing):
        assert _mod_list(nothing) == []


class TestTheScoreItself:
    @staticmethod
    def _judged():
        return {
            "theirs": {"300": 563, "100": 125, "50": 20, "miss": 51},
            "ours": {"300": 562, "100": 120, "50": 16, "miss": 61},
            "their_accuracy": 80.1054,
            "our_accuracy": 79.6662,
            "their_max_combo": 283,
            "mods": "NFHDV2",
            "finished": True,
            "player": "Uika Misumi",
        }

    def test_the_card_shows_what_the_player_got_not_what_the_engine_thinks(self):
        """The engine's own reading is a different question, asked when
        something looks wrong rather than every time, and it lives under the
        buttons. A card that quietly showed our counts would be the bot
        disagreeing with the game on the one screen nobody reads twice."""
        score = score_from_replay(self._judged(), {"id": 1})
        assert score["statistics"]["count_300"] == 563
        assert score["statistics"]["count_miss"] == 51

    def test_accuracy_is_handed_over_as_the_api_states_it(self):
        """A fraction, not a percentage — the card divides by nothing."""
        score = score_from_replay(self._judged(), {"id": 1})
        assert 0.80 < score["accuracy"] < 0.81

    def test_the_map_comes_from_the_api_record(self):
        beatmap = {"id": 9, "beatmapset": {"artist": "xi"}}
        score = score_from_replay(self._judged(), beatmap)
        assert score["beatmap"] is beatmap
        assert score["beatmapset"] == {"artist": "xi"}

    def test_a_replay_with_no_map_record_still_produces_a_score(self):
        score = score_from_replay(self._judged(), None)
        assert score["beatmap"] == {} and score["beatmapset"] == {}
