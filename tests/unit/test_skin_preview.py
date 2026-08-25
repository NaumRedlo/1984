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
           cursor: bool = True, square: bool = False) -> str:
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

    # The part that wears the combo colour. `square` is how one skin is made to
    # differ from another in the only way that counts now: its drawing.
    art, draw, box = canvas(64)
    if square:
        draw.rectangle(box, fill=(255, 255, 255, 255))
    else:
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
    extra steps.

    The difference has to come from the *art*, since the colours are the same
    for every skin — so these two differ in the shape of their circle, which is
    the thing a person is actually looking at.
    """
    round_one = preview.draw(a_skin(tmp_path, "round"))
    square_one = preview.draw(a_skin(tmp_path, "square", square=True))

    # Compared pixel for pixel rather than by some statistic of one channel.
    # The first version counted red pixels, which mostly counted the parts the
    # two skins share — the score, the mark, the cursor — and reported a six
    # per cent difference between a circle and a square.
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
    """A render takes its combo colours from the *map*, not the skin. A
    preview drawn in whatever `skin.ini` declares shows a palette that never
    appears in a video made with it — which is what it was doing, and what the
    first person to look at the grid noticed.

    It also makes the grid comparable: what differs between two thumbnails is
    the art rather than what each author wrote in their `[Colours]`.
    """
    red = preview.draw(a_skin(tmp_path, "says-red", combo="255,0,0"))
    blue = preview.draw(a_skin(tmp_path, "says-blue", combo="0,0,255"))
    assert _average(red) == _average(blue), (
        f"the skin's declared colour reached the picture: "
        f"{_average(red)} against {_average(blue)}"
    )


def test_the_picture_is_a_circle_and_a_cursor_and_not_a_scene(tmp_path):
    """A version with two circles, the skin's score face and its `hit300` was
    tried. At the size a grid shows these, the extra pieces are clutter that
    makes every skin look like every other skin — so the picture is of the one
    thing a person recognises.

    The rest of a skin is worth showing on a screen of its own, which is a
    different job from this one.
    """
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

    first = list(Image.open(preview.path_of("mine")).convert("RGBA").getdata())

    import time

    time.sleep(0.01)
    # Something that is actually drawn. The first version of this edited the
    # skin's `[Colours]`, which stopped meaning anything the moment previews
    # stopped reading them — a test that passed by accident and then failed by
    # accident.
    a_skin(tmp_path / "skins", "mine-square", square=True)
    for leaf in os.listdir(tmp_path / "skins" / "mine-square"):
        os.replace(
            tmp_path / "skins" / "mine-square" / leaf,
            tmp_path / "skins" / "mine" / leaf,
        )
    os.utime(folder, None)
    second = list(Image.open(preview.path_of("mine")).convert("RGBA").getdata())

    assert first != second, "the old picture was kept"
