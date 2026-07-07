"""The `map` command's hypothetical performance calculator
(utils/osu/pp_calculator.calculate_whatif_pp/_calc_whatif_sync) and the mod
parsing/validation it shares with the rest of pp_calculator."""

from utils.osu import pp_calculator
from utils.osu.mod_utils import KNOWN_PP_MODS, MOD_BITS, parse_mods_tokens


class _FakeState:
    n300, n100, n50, misses, max_combo = 550, 8, 0, 0, 720


class _FakeDifficulty:
    stars = 7.42
    max_combo = 720


class _FakeResult:
    def __init__(self, pp=227.31):
        self.pp = pp
    difficulty = _FakeDifficulty()
    state = _FakeState()


class _FakePerformance:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def calculate(self, beatmap):
        # pp scales with the queried accuracy so the bracket table is
        # actually distinguishable in tests, not just N identical values.
        acc = self.kwargs.get("accuracy", 94.0)
        return _FakeResult(pp=round(acc * 3, 2))


class _FakeRosu:
    Beatmap = staticmethod(lambda bytes: object())
    Performance = _FakePerformance


def test_calc_whatif_sync_returns_hit_breakdown_from_state(monkeypatch):
    monkeypatch.setattr(pp_calculator, "rosu", _FakeRosu)
    out = pp_calculator._calc_whatif_sync(b"x", 0, 94.0)
    assert out == {
        "pp": 282.0, "star_rating": 7.42, "max_combo": 720, "combo": 720,
        "count_300": 550, "count_100": 8, "count_50": 0, "count_miss": 0,
        "brackets": {95.0: 285.0, 98.0: 294.0, 99.0: 297.0, 100.0: 300.0},
    }


def test_calc_whatif_sync_falls_back_to_difficulty_combo_when_state_missing(monkeypatch):
    class _NoStateResult(_FakeResult):
        state = None

    class _Perf(_FakePerformance):
        def calculate(self, beatmap):
            return _NoStateResult()

    monkeypatch.setattr(pp_calculator, "rosu", type("R", (), {
        "Beatmap": staticmethod(lambda bytes: object()), "Performance": _Perf,
    }))
    out = pp_calculator._calc_whatif_sync(b"x", 0, 100.0)
    assert out["combo"] == 720  # falls back to difficulty.max_combo
    assert out["count_300"] == 0 and out["count_miss"] == 0


async def test_calculate_whatif_pp_none_when_rosu_unavailable(monkeypatch):
    monkeypatch.setattr(pp_calculator, "rosu", None)
    assert await pp_calculator.calculate_whatif_pp(123, 94.0, "HR") is None


async def test_calculate_whatif_pp_none_when_download_fails(monkeypatch):
    monkeypatch.setattr(pp_calculator, "rosu", _FakeRosu)

    async def _no_download(_bid):
        return None
    monkeypatch.setattr(pp_calculator, "_download_osu_file", _no_download)
    assert await pp_calculator.calculate_whatif_pp(123, 94.0) is None


async def test_calculate_whatif_pp_happy_path(monkeypatch):
    monkeypatch.setattr(pp_calculator, "rosu", _FakeRosu)

    async def _fake_download(_bid):
        return b"fake .osu bytes"
    monkeypatch.setattr(pp_calculator, "_download_osu_file", _fake_download)

    out = await pp_calculator.calculate_whatif_pp(123, 94.0, "HR")
    assert out["pp"] == 282.0 and out["count_300"] == 550
    assert out["brackets"] == {95.0: 285.0, 98.0: 294.0, 99.0: 297.0, 100.0: 300.0}


def test_parse_mods_matches_mod_bits():
    assert pp_calculator._parse_mods("HDDT") == MOD_BITS["HD"] | MOD_BITS["DT"]
    assert pp_calculator._parse_mods("") == 0
    assert pp_calculator._parse_mods("XY") == 0  # unknown acronym contributes nothing


def test_parse_mods_tokens_splits_pairs():
    assert parse_mods_tokens("HDDT") == ("HD", "DT")
    assert parse_mods_tokens("") == ()
    assert parse_mods_tokens("HR") == ("HR",)


def test_known_pp_mods_matches_mod_bits_keys():
    assert KNOWN_PP_MODS == frozenset(MOD_BITS)
    assert "HR" in KNOWN_PP_MODS and "XY" not in KNOWN_PP_MODS
