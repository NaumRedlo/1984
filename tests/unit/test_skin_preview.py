"""A picture of a skin, small enough to pick one out of a grid.

The bot lists skins as a column of buttons with names on them, which is the
worst possible way to choose between things whose entire point is how they
look.

What is worth testing here is not that a PNG comes out — it is that two skins
which differ come out looking different, and that a skin whose author padded
its elements is not drawn smaller than one who cropped theirs. Both were wrong
in the first version, and neither would have shown up in "does it write a
file".
"""

import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services.dossier import preview  # noqa: E402


def a_skin(tmp_path, name: str, *, pad=0, combo: str | None = None,
           cursor: bool = True) -> str:
    """A folder with the elements a preview is drawn from.

    Shaped the way a real skin shapes them, which matters more than it sounds.
    The first version of this drew `hitcircleoverlay` as a solid square the
    same size as the circle — so it covered the tinted circle completely and
    every skin came out identical, and the test that was meant to catch
    exactly that failed against correct code.

    `pad` is transparent margin around each element: the thing skin authors do
    differently from one another, and which the drawing has to see past.
    """
    from PIL import ImageDraw

    folder = tmp_path / name
    folder.mkdir()

    def canvas(size: int):
        art = Image.new("RGBA", (size + pad * 2, size + pad * 2), (0, 0, 0, 0))
        return art, ImageDraw.Draw(art), (pad, pad, pad + size - 1, pad + size - 1)

    # A filled disc: the part that wears the combo colour.
    art, draw, box = canvas(64)
    draw.ellipse(box, fill=(255, 255, 255, 255))
    art.save(folder / "hitcircle.png")

    # A ring, transparent in the middle — which is what lets the colour under
    # it be seen at all.
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
    """No preview rather than a picture of our own fallbacks — the grid says
    the name instead, which is honest about there being nothing to see."""
    empty = tmp_path / "empty"
    empty.mkdir()
    assert preview.draw(str(empty)) is None


def _average(picture) -> tuple[int, int, int]:
    small = picture.convert("RGB").resize((1, 1), Image.LANCZOS)
    return small.getpixel((0, 0))


def test_two_skins_that_differ_look_different(tmp_path):
    """The whole job. A grid of identical thumbnails is a list of names with
    extra steps."""
    red = preview.draw(a_skin(tmp_path, "red", combo="255,0,0"))
    blue = preview.draw(a_skin(tmp_path, "blue", combo="0,0,255"))

    got_red, got_blue = _average(red), _average(blue)
    assert got_red[0] > got_blue[0] + 10, f"{got_red} against {got_blue}"
    assert got_blue[2] > got_red[2] + 10, f"{got_red} against {got_blue}"


def test_the_skins_own_colour_is_worn_by_the_circle(tmp_path):
    """osu! tints the circle and the approach ring with the combo colour, and
    following that is what makes two skins with the same shapes look as
    different here as they do in the game."""
    green = _average(preview.draw(a_skin(tmp_path, "green", combo="0,255,0")))
    assert green[1] > green[0] and green[1] > green[2]


def test_a_skin_with_no_colours_still_gets_one(tmp_path):
    picture = preview.draw(a_skin(tmp_path, "colourless"))
    assert picture is not None
    assert _average(picture) != (0, 0, 0)


def test_padding_around_an_element_does_not_shrink_it(tmp_path):
    """Skins pad their elements and no two pad them the same. A cursor drawn as
    a dot in the middle of a 128-pixel canvas and one cropped to its own edges
    are the same cursor in the game — measuring the file rather than the
    drawing made the first a third of the size of the second, and a grid where
    that decides the size compares padding instead of skins.
    """
    tight = preview.draw(a_skin(tmp_path, "tight", pad=0, combo="255,0,0"))
    padded = preview.draw(a_skin(tmp_path, "padded", pad=90, combo="255,0,0"))

    # The same drawing at the same size means the same amount of ink.
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
    """`packed()` zips whatever is in a skin folder, so a preview in there
    would travel to every worker that ever rendered in that skin."""
    store = tmp_path / "skins"
    store.mkdir()
    a_skin(store, "mine", combo="0,0,255")
    monkeypatch.setattr(preview, "store_dir", lambda: str(store))
    monkeypatch.setattr(preview, "folder_of", lambda name: str(store / name))

    where = preview.path_of("mine")
    assert where and os.path.isfile(where)
    assert os.path.basename(os.path.dirname(where)) == ".previews"
    assert not os.path.exists(store / "mine" / "preview.png")


def test_a_preview_is_redrawn_when_the_skin_changes(tmp_path, monkeypatch):
    """Somebody sending the `.osk` again is rare, and a stale thumbnail of the
    old one is exactly the kind of wrong nobody reports."""
    store = tmp_path / "skins"
    store.mkdir()
    folder = a_skin(store, "mine", combo="255,0,0")
    monkeypatch.setattr(preview, "store_dir", lambda: str(store))
    monkeypatch.setattr(preview, "folder_of", lambda name: str(store / name))

    first = _average(Image.open(preview.path_of("mine")))

    import time

    time.sleep(0.01)
    (tmp_path / "skins" / "mine" / "skin.ini").write_text("[Colours]\nCombo1: 0,0,255\n")
    os.utime(folder, None)
    second = _average(Image.open(preview.path_of("mine")))

    assert second[2] > first[2] + 10, f"the old picture was kept: {first} then {second}"
