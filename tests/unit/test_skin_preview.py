import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services.dossier import preview

def a_skin(tmp_path, name: str, *, pad=0, combo: str | None = None,
           cursor: bool = True, square: bool = False) -> str:
    from PIL import ImageDraw

    folder = tmp_path / name
    folder.mkdir()

    def canvas(size: int):
        art = Image.new("RGBA", (size + pad * 2, size + pad * 2), (0, 0, 0, 0))
        return art, ImageDraw.Draw(art), (pad, pad, pad + size - 1, pad + size - 1)

    art, draw, box = canvas(64)
    if square:
        draw.rectangle(box, fill=(255, 255, 255, 255))
    else:
        draw.ellipse(box, fill=(255, 255, 255, 255))
    art.save(folder / "hitcircle.png")

    art, draw, box = canvas(64)
    draw.ellipse(box, outline=(255, 255, 255, 255), width=5)
    art.save(folder / "hitcircleoverlay.png")

    art, draw, box = canvas(64)
    draw.ellipse(box, outline=(255, 255, 255, 255), width=3)
    art.save(folder / "approachcircle.png")

    art, draw, box = canvas(24)
    draw.rectangle(box, fill=(255, 255, 255, 255))
    art.save(folder / "default-1.png")

    if cursor:
        art, draw, box = canvas(32)
        draw.ellipse(box, fill=(255, 255, 255, 255))
        art.save(folder / "cursor.png")

    if combo is not None:
        (folder / "skin.ini").write_text(f"[Colours]\nCombo1: {combo}\n")
    return str(folder)

def test_a_skin_with_a_hit_circle_gets_a_picture(tmp_path):
    picture = preview.draw(a_skin(tmp_path, "plain"))
    assert picture is not None
    assert picture.size == preview.SIZE

def test_a_folder_with_nothing_in_it_gets_no_picture(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert preview.draw(str(empty)) is None

def _average(picture) -> tuple[int, int, int]:
    small = picture.convert("RGB").resize((1, 1), Image.LANCZOS)
    return small.getpixel((0, 0))

def test_two_skins_that_differ_look_different(tmp_path):
    round_one = preview.draw(a_skin(tmp_path, "round"))
    square_one = preview.draw(a_skin(tmp_path, "square", square=True))

    differing = sum(
        1 for here, there in zip(round_one.getdata(), square_one.getdata())
        if here != there
    )
    total = round_one.width * round_one.height
    assert differing > total * 0.02, (
        f"only {differing / total:.1%} of the picture changed between a skin "
        f"drawn with round circles and one drawn with square ones"
    )

def test_the_skins_own_combo_colours_are_ignored(tmp_path):
    red = preview.draw(a_skin(tmp_path, "says-red", combo="255,0,0"))
    blue = preview.draw(a_skin(tmp_path, "says-blue", combo="0,0,255"))
    assert _average(red) == _average(blue), (
        f"the skin's declared colour reached the picture: "
        f"{_average(red)} against {_average(blue)}"
    )

def test_the_picture_is_a_circle_and_a_cursor_and_not_a_scene(tmp_path):
    with_extras = a_skin(tmp_path, "rich")
    for leaf in ("score-1.png", "hit300.png"):
        Image.new("RGBA", (200, 60), (255, 0, 0, 255)).save(
            os.path.join(with_extras, leaf)
        )

    picture = preview.draw(with_extras)
    red = sum(
        1 for pixel in picture.convert("RGBA").getdata()
        if pixel[0] > 200 and pixel[1] < 60 and pixel[2] < 60
    )
    assert red == 0, f"{red} pixels of something a thumbnail does not want"

def test_padding_around_an_element_does_not_shrink_it(tmp_path):
    tight = preview.draw(a_skin(tmp_path, "tight", pad=0, combo="255,0,0"))
    padded = preview.draw(a_skin(tmp_path, "padded", pad=90, combo="255,0,0"))

    def ink(picture):
        return sum(1 for pixel in picture.convert("RGBA").getdata() if pixel[0] > 120)

    close = abs(ink(tight) - ink(padded)) / max(ink(tight), 1)
    assert close < 0.12, (
        f"padding changed the drawn size by {close:.0%} — "
        f"{ink(tight)} against {ink(padded)}"
    )

def test_a_preview_is_written_beside_the_store_and_not_inside_the_skin(
    tmp_path, monkeypatch
):
    store = tmp_path / "skins"
    store.mkdir()
    a_skin(store, "mine", combo="0,0,255")
    monkeypatch.setattr(preview, "store_dir", lambda: str(store))
    monkeypatch.setattr(preview, "folder_of", lambda name: str(store / name))

    where = preview.path_of("mine")
    assert where and os.path.isfile(where)

    assert os.path.basename(os.path.dirname(os.path.dirname(where))) == ".previews"
    assert not os.path.exists(store / "mine" / "preview.png")

def test_a_preview_is_redrawn_when_the_skin_changes(tmp_path, monkeypatch):
    store = tmp_path / "skins"
    store.mkdir()
    folder = a_skin(store, "mine", combo="255,0,0")
    monkeypatch.setattr(preview, "store_dir", lambda: str(store))
    monkeypatch.setattr(preview, "folder_of", lambda name: str(store / name))

    first = list(Image.open(preview.path_of("mine")).convert("RGBA").getdata())

    import time

    time.sleep(0.01)

    a_skin(tmp_path / "skins", "mine-square", square=True)
    for leaf in os.listdir(tmp_path / "skins" / "mine-square"):
        os.replace(
            tmp_path / "skins" / "mine-square" / leaf,
            tmp_path / "skins" / "mine" / leaf,
        )
    os.utime(folder, None)
    second = list(Image.open(preview.path_of("mine")).convert("RGBA").getdata())

    assert first != second, "the old picture was kept"

def test_a_change_to_the_drawing_redraws_everything(tmp_path, monkeypatch):
    store = tmp_path / "skins"
    store.mkdir()
    a_skin(store, "mine")
    monkeypatch.setattr(preview, "store_dir", lambda: str(store))
    monkeypatch.setattr(preview, "folder_of", lambda name: str(store / name))

    first = preview.path_of("mine")
    assert first and os.path.isfile(first)

    was = _average(Image.open(first))

    monkeypatch.setattr(preview, "DRAWING", preview.DRAWING + 1)
    monkeypatch.setattr(preview, "CIRCLE_SHARE", preview.CIRCLE_SHARE * 0.6)
    second = preview.path_of("mine")

    assert second != first, "the same file was handed back after the code changed"
    assert _average(Image.open(second)) != was

def test_the_pictures_the_old_code_drew_do_not_pile_up(tmp_path, monkeypatch):
    store = tmp_path / "skins"
    store.mkdir()
    a_skin(store, "mine")
    monkeypatch.setattr(preview, "store_dir", lambda: str(store))
    monkeypatch.setattr(preview, "folder_of", lambda name: str(store / name))
    preview.path_of("mine")

    monkeypatch.setattr(preview, "DRAWING", preview.DRAWING + 1)
    preview.path_of("mine")

    versions = os.listdir(store / ".previews")
    assert versions == [f"v{preview.DRAWING}"], f"left behind: {versions}"

def test_a_skin_ini_full_of_slashes_still_gives_up_its_prefixes(tmp_path):
    folder = a_skin(tmp_path, "commented")
    (tmp_path / "commented" / "skin.ini").write_text(
        "//The prefix for the hit circle font\n"
        "[Fonts]\n"
        "HitCirclePrefix: numbers/mine\n"
        "//Colours\n"
        "[Colours]\n"
        "Combo1: 1,2,3\n"
    )
    flat = preview._settings(preview._ini(folder))
    assert flat.get("hitcircleprefix") == "numbers/mine"
    assert preview._prefix(flat, "hitcircleprefix", "default") == "numbers/mine"

def test_the_circle_is_shown_as_its_author_drew_it(tmp_path):
    pale = a_skin(tmp_path, "pale")
    Image.new("RGBA", (64, 64), (255, 255, 255, 255)).save(
        os.path.join(pale, "hitcircle.png")
    )
    dark = a_skin(tmp_path, "dark")
    Image.new("RGBA", (64, 64), (90, 90, 90, 255)).save(
        os.path.join(dark, "hitcircle.png")
    )

    def circle_colour(picture):
        middle = picture.convert("RGBA").getpixel(
            (round(preview.SIZE[0] * 0.40), preview.SIZE[1] // 2 + 40)
        )
        return middle[:3]

    for got in (circle_colour(preview.draw(pale)), circle_colour(preview.draw(dark))):
        assert max(got) - min(got) < 24, f"a colour was put on the circle: {got}"
