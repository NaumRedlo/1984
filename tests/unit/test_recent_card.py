"""Headless render + strain checks for the redesigned recent-score card
(services/image/render/recent.py + utils/osu/pp_calculator.calculate_strains).
No network: images are None and strains are passed directly."""

from services.image.core import CardRenderer
from utils.osu import pp_calculator


def _sample(passed=True, title="Anoyo-iki no Bus ni Notte Saraba."):
    return {
        "artist": "TUYU", "title": title, "version": "Hard", "mapper_name": "SnowNiNo_",
        "star_rating": 5.14, "total_length": 126, "total_objects": 360,
        "accuracy": 88.43 if not passed else 99.1, "combo": 48 if not passed else 720,
        "max_combo": 720, "misses": 1 if not passed else 0,
        "pp": 226, "pp_if_fc": 246, "rank_grade": "F" if not passed else "S",
        "count_300": 30, "count_100": 5, "count_50": 1, "username": "kazaki1865",
        "passed": passed, "beatmap_status": "ranked", "mods": "HDDT",
        "cs": 4.0, "ar": 9.7, "od": 9.1, "hp": 4.0,
        "played_at": "2026-06-28T16:38:00+00:00", "bpm": 180,
    }


def _render(data, strains):
    buf = CardRenderer().generate_recent_card(data, None, None, None, None, strains)
    return buf.getvalue()


def test_renders_fail_and_pass():
    strains = [i / 63 for i in range(64)]
    for passed in (False, True):
        png = _render(_sample(passed=passed), strains)
        assert png.startswith(b"\x89PNG") and len(png) > 2000


def test_renders_without_strains():
    # None strains -> the graph shows NO DATA but the card still renders.
    png = _render(_sample(passed=False), None)
    assert png.startswith(b"\x89PNG")


def test_renders_japanese_title():
    # Non-Latin title must not raise (CJK fallback font path).
    png = _render(_sample(title="ここからはじまるプロローグ。"), [0.5] * 64)
    assert png.startswith(b"\x89PNG")


def test_renders_russian_lang():
    # lang="ru" swaps the UI labels to Russian (Cyrillic fallback font path).
    data = _sample(passed=False)
    data["lang"] = "ru"
    png = _render(data, [0.5] * 64)
    assert png.startswith(b"\x89PNG") and len(png) > 2000


def test_renders_default_lang_when_missing():
    # No "lang" key at all -> defaults to English, same as before this feature.
    data = _sample(passed=True)
    assert "lang" not in data
    png = _render(data, [0.5] * 64)
    assert png.startswith(b"\x89PNG")


async def test_calculate_strains_none_when_download_fails(monkeypatch):
    async def _no_download(_bid):
        return None
    monkeypatch.setattr(pp_calculator, "_download_osu_file", _no_download)
    assert await pp_calculator.calculate_strains(123, "HDDT") is None


def test_strains_sync_normalizes_and_downsamples(monkeypatch):
    # Fake rosu so the pure downsample/normalize logic is testable offline.
    class _S:
        aim = [float(i) for i in range(200)]
        speed = [0.0] * 200
    class _FakeDiff:
        def __init__(self, mods=0): pass
        def strains(self, _bm): return _S()
    class _FakeRosu:
        Beatmap = staticmethod(lambda bytes: object())
        Difficulty = _FakeDiff
    monkeypatch.setattr(pp_calculator, "rosu", _FakeRosu)
    out = pp_calculator._strains_sync(b"x", 0, 64)
    assert out is not None and len(out) == 64
    assert 0.0 <= min(out) and max(out) <= 1.0 and max(out) > 0.9
