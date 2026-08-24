from utils.osu import pp_calculator


class _Result:
    pp = 100.0

    class difficulty:
        stars = 7.42
        max_combo = 720


class _Performance:
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
    made = _calc(monkeypatch, n300=390, n100=10, n50=0, misses=0, total_objects=1704)
    assert made[0].passed_objects == 400


def test_a_play_that_covered_the_map_is_left_alone(monkeypatch):
    made = _calc(monkeypatch, n300=1690, n100=10, n50=4, misses=0, total_objects=1704)
    assert made[0].passed_objects is None


def test_misses_count_as_objects_the_play_reached(monkeypatch):
    made = _calc(monkeypatch, n300=1600, n100=90, n50=4, misses=10, total_objects=1704)
    assert made[0].passed_objects is None


def test_without_a_map_size_nothing_is_assumed(monkeypatch):
    made = _calc(monkeypatch, n300=390, n100=10, n50=0, misses=0, total_objects=0)
    assert made[0].passed_objects is None


def test_if_fc_on_a_stopped_play_asks_about_the_whole_map(monkeypatch):
    made = _calc(monkeypatch, n300=390, n100=10, n50=0, misses=0, total_objects=1704)
    fc = made[1]
    assert fc.passed_objects is None
    assert "accuracy" in fc.kwargs
    assert fc.kwargs.get("misses") == 0
    # (300*390 + 100*10) / (300*400) is 98.33%, and there were no misses to make good.
    assert round(fc.kwargs["accuracy"], 2) == 98.33


def test_if_fc_on_a_finished_play_keeps_the_exact_hits(monkeypatch):
    made = _calc(monkeypatch, n300=1600, n100=90, n50=4, misses=10, total_objects=1704)
    fc = made[1]
    assert fc.kwargs.get("n300") == 1610
    assert fc.kwargs.get("n100") == 90
    assert fc.kwargs.get("misses") == 0
