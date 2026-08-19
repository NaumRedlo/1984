"""The star rating shown against a play is the one the play had.

The API hands back the map's nominal rating on every score and the cards used
to draw it, which is the wrong number for any play with HD, DT, HR, EZ, HT, FL
or TD on it — most of a top hundred. These cover the two halves of the fix: the
table that decides whether a play's mods can move the figure at all, and the
helper that asks ppy for the moved one.
"""

import asyncio

import pytest

from utils.osu.api_client import _SR_MOD_BITS, _sr_mods_bitset
from utils.osu import star_rating


class _Client:
    """Stands in for the API client, counting what it was asked."""

    def __init__(self, answer=9.99, blow_up=False):
        self.answer, self.blow_up, self.asked = answer, blow_up, []

    async def effective_sr(self, beatmap_id, mods, nominal):
        self.asked.append((beatmap_id, mods, nominal))
        if self.blow_up:
            raise RuntimeError("the endpoint is having a day")
        # The real one returns the nominal figure untouched when the mods
        # cannot move it, which is what makes a nomod page free.
        return self.answer if _sr_mods_bitset(mods) else nominal


def test_hidden_is_a_mod_that_moves_the_rating():
    # Measured against ppy's own attributes endpoint, one mod at a time, on
    # three maps: HD is +0.441, +0.433, +0.328. The table this replaced said in
    # so many words that HD did not alter SR — true of the old algorithm, and
    # the reason every HD play kept its nominal figure.
    assert "HD" in _SR_MOD_BITS
    assert _sr_mods_bitset("HD") != 0
    # And the ones that genuinely cannot move it stay out, so they cost no call.
    for quiet in ("NF", "SD", "RX", "SO", "PF", "CL"):
        assert quiet not in _SR_MOD_BITS, quiet
        assert _sr_mods_bitset(quiet) == 0, quiet


@pytest.mark.parametrize("written", ["HD,DT", "HDDT", "hddt", ["HD", "DT"], ("HD", "DT")])
def test_every_spelling_of_the_mods_is_read(written):
    # Rows and the API client join with commas; the cards build a bare string;
    # the API itself hands back a list. Splitting on commas alone found nothing
    # in the second, and that failed quietly — no error, no call, and the
    # nominal figure served as though the mods had been weighed.
    assert _sr_mods_bitset(written) == _SR_MOD_BITS["HD"] | _SR_MOD_BITS["DT"]


def test_mods_that_cannot_move_it_are_not_worth_a_call():
    assert _sr_mods_bitset("NF,SD,CL") == 0
    assert _sr_mods_bitset("") == 0
    assert _sr_mods_bitset(None) == 0


def _run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


def test_the_played_rating_lands_on_the_row():
    rows = [{"beatmap_id": 1, "mods": ["HD", "DT"], "star_rating": 6.67}]
    client = _Client(answer=10.58)
    _run(star_rating.fill(client, rows))
    assert rows[0]["eff_sr"] == 10.58
    # The nominal figure is left where it is: it is a fact about the map and
    # something else may still want it.
    assert rows[0]["star_rating"] == 6.67


def test_a_row_whose_mods_do_not_matter_keeps_its_figure():
    rows = [{"beatmap_id": 1, "mods": ["NF", "CL"], "star_rating": 6.67}]
    _run(star_rating.fill(_Client(), rows))
    assert rows[0]["eff_sr"] == 6.67


def test_a_stale_resolved_figure_is_asked_again_not_trusted():
    # Rows resolved before HD was in the table carry their nominal rating in
    # `eff_sr`, and nothing distinguishes those from a play whose mods really
    # did not matter. So the nominal figure is what gets sent as the fallback,
    # and the answer replaces whatever was there.
    rows = [{"beatmap_id": 1, "mods": ["HD"], "star_rating": 6.67, "eff_sr": 6.67}]
    client = _Client(answer=7.11)
    _run(star_rating.fill(client, rows))
    assert rows[0]["eff_sr"] == 7.11
    assert client.asked == [(1, ["HD"], 6.67)]


def test_a_failing_endpoint_costs_the_figure_and_not_the_card():
    # A card with a slightly stale rating on it is worth more than a card that
    # did not draw.
    rows = [{"beatmap_id": 1, "mods": ["HD"], "star_rating": 6.67}]
    _run(star_rating.fill(_Client(blow_up=True), rows))
    assert rows[0].get("eff_sr", 6.67) == 6.67


def test_without_a_client_nothing_is_touched_and_nothing_raises():
    rows = [{"beatmap_id": 1, "mods": ["HD"], "star_rating": 6.67}]
    _run(star_rating.fill(None, rows))
    assert "eff_sr" not in rows[0]


def test_rows_with_no_map_are_skipped_rather_than_asked_about():
    rows = [{"mods": ["HD"], "star_rating": 6.67}, {"beatmap_id": 0, "mods": ["HD"]}]
    client = _Client()
    _run(star_rating.fill(client, rows))
    assert client.asked == []
