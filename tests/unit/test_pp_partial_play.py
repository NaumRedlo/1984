"""A play that stopped partway is scored against the part it reached.

Reported as "my bot says 395 for this score, another one says 193.24". A player
who fails or quits has no misses to show for it — they simply stopped — so a
calculator that is not told where they stopped sees a near-perfect run and
credits it with the whole map's difficulty. On the map this was measured on, a
quarter-way play worth 237 came out at 450.

The card already knew: it draws the completion ring from the same numbers. Only
the pp calculation was never handed them.
"""

from utils.osu import pp_calculator


class _Result:
    pp = 100.0

    class difficulty:
        stars = 7.42
        max_combo = 720


class _Performance:
    """Records what it was told, so the decision can be tested without rosu."""

    made = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.passed_objects = None
        _Performance.made.append(self)

    def set_passed_objects(self, n):
        self.passed_objects = n

    def calculate(self, beatmap):
        return _Result()


class _Rosu:
    Beatmap = staticmethod(lambda bytes: object())
    Performance = _Performance


def _calc(monkeypatch, *, n300, n100, n50, misses, total_objects):
    monkeypatch.setattr(pp_calculator, "rosu", _Rosu)
    _Performance.made = []
    pp_calculator._calc_sync(
        b"map", 0, 98.0, 500, misses, n300, n100, n50, total_objects,
    )
    return _Performance.made


def test_a_play_that_stopped_is_scored_on_what_it_reached(monkeypatch):
    # 400 objects judged out of 1704: the play covers under a quarter of the
    # map, and the difficulty it is scored against has to be that quarter.
    made = _calc(monkeypatch, n300=390, n100=10, n50=0, misses=0, total_objects=1704)
    assert made[0].passed_objects == 400


def test_a_play_that_covered_the_map_is_left_alone(monkeypatch):
    # Every object judged, so there is nothing to cut short — and saying so
    # anyway would be a needless way to get it wrong if the counts ever drift.
    made = _calc(monkeypatch, n300=1690, n100=10, n50=4, misses=0, total_objects=1704)
    assert made[0].passed_objects is None


def test_misses_count_as_objects_the_play_reached(monkeypatch):
    # A miss is an object that was judged. Leaving them out would make every
    # play with misses look like it stopped early.
    made = _calc(monkeypatch, n300=1600, n100=90, n50=4, misses=10, total_objects=1704)
    assert made[0].passed_objects is None


def test_without_a_map_size_nothing_is_assumed(monkeypatch):
    # Callers that cannot say how big the map is get the old behaviour rather
    # than a guess: truncating a complete play would be worse than the bug.
    made = _calc(monkeypatch, n300=390, n100=10, n50=0, misses=0, total_objects=0)
    assert made[0].passed_objects is None


def test_if_fc_on_a_stopped_play_asks_about_the_whole_map(monkeypatch):
    """"If FC" on a play that stopped means finishing it.

    Asked with the partial counts it would have the same disease as the figure
    above — a quarter of the hits against all of the difficulty — so for a
    stopped play it is asked by accuracy over the whole map instead, and is not
    cut short.
    """
    made = _calc(monkeypatch, n300=390, n100=10, n50=0, misses=0, total_objects=1704)
    fc = made[1]
    assert fc.passed_objects is None
    assert "accuracy" in fc.kwargs
    assert fc.kwargs.get("misses") == 0
    # (300*390 + 100*10) / (300*400) is 98.33%, and there were no misses to make good.
    assert round(fc.kwargs["accuracy"], 2) == 98.33


def test_if_fc_on_a_finished_play_keeps_the_exact_hits(monkeypatch):
    # Nothing to extrapolate when the play covered the map: the misses become
    # threes and the rest of the distribution is used as it stands.
    made = _calc(monkeypatch, n300=1600, n100=90, n50=4, misses=10, total_objects=1704)
    fc = made[1]
    assert fc.kwargs.get("n300") == 1610
    assert fc.kwargs.get("n100") == 90
    assert fc.kwargs.get("misses") == 0
