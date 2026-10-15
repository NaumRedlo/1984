from services.image.core import CardRenderer

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

    png = _render(_sample(passed=False), None)
    assert png.startswith(b"\x89PNG")

def test_renders_japanese_title():

    png = _render(_sample(title="ここからはじまるプロローグ。"), [0.5] * 64)
    assert png.startswith(b"\x89PNG")

def test_renders_russian_lang():

    data = _sample(passed=False)
    data["lang"] = "ru"
    png = _render(data, [0.5] * 64)
    assert png.startswith(b"\x89PNG") and len(png) > 2000

def test_renders_default_lang_when_missing():

    data = _sample(passed=True)
    assert "lang" not in data
    png = _render(data, [0.5] * 64)
    assert png.startswith(b"\x89PNG")



def _picture(data):
    from io import BytesIO

    from PIL import Image

    return Image.open(BytesIO(_render(data, [0.5] * 64))).convert("RGB")


def _changed(one, other):
    from PIL import ImageChops

    return ImageChops.difference(one, other).getbbox()


def test_the_score_stands_beside_the_grade_and_the_title_gives_it_room():
    plain = _picture(_sample())
    left, top, right, bottom = _changed(plain, _picture(dict(_sample(), total_score=1087654321)))
    assert 640 <= left and right <= 1110
    assert 80 <= top and bottom <= 232
    assert _changed(plain, _picture(dict(_sample(), total_score=0))) is None


def test_the_score_keeps_its_right_edge_by_the_grade_however_long_it_is():
    plain = _picture(_sample())
    short = _changed(plain, _picture(dict(_sample(), total_score=184320)))
    long = _changed(plain, _picture(dict(_sample(), total_score=1087654321)))
    assert long[0] < short[0]
    assert abs(long[2] - short[2]) <= 2


def test_mods_hang_under_the_hit_counts_and_the_overflow_is_counted():
    plain = _picture(dict(_sample(), mods=""))
    left, top, right, bottom = _changed(plain, _picture(_sample()))
    assert 840 <= left and right <= 1232
    assert 340 <= top and bottom <= 374
    many = _changed(plain, _picture(dict(_sample(), mods="HDHRDTFLNFSDEZPFSOTDBLCLMR")))
    assert 840 <= many[0] and many[2] <= 1232 and many[3] <= 374


def test_the_place_among_the_best_sits_in_the_top_right_corner_of_the_player_panel():
    plain = _picture(_sample())
    left, top, right, bottom = _changed(plain, _picture(dict(_sample(), top_place=12)))
    assert 180 <= left and right <= 330
    assert 408 <= top and bottom <= 436
    assert _changed(plain, _picture(dict(_sample(), top_place=None))) is None


def test_the_player_panel_comes_first_and_names_the_client_under_the_player():
    plain = _picture(_sample())
    for client in ("lazer", "stable"):
        left, top, right, bottom = _changed(plain, _picture(dict(_sample(), score_client=client)))
        assert 24 <= left and right <= 330
        assert 398 <= top and 600 <= bottom <= 630
    assert _changed(_picture(dict(_sample(), score_client="lazer")), _picture(dict(_sample(), score_client="stable"))) is not None
    assert _changed(plain, _picture(dict(_sample(), score_client="unknown"))) is None
