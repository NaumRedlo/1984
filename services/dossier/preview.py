"""A picture of a skin, small enough to pick one out of a grid.

The bot lists skins as a column of buttons with names on them, which is the
worst way to choose between things whose whole point is how they look. The
mini-app wants a grid, and a grid wants pictures.

## Why this is not a rendered frame

The engine can draw a real frame in any skin, and that was the first idea: the
most faithful preview possible, the actual renderer, the actual skin. It is the
wrong tool at this size. A gameplay frame shrunk to a thumbnail is a playfield
of specks and a HUD nobody can read — it shows *less* than the three elements
below, not more. What identifies a skin to somebody choosing one is its hit
circle and its cursor, which is why every skin listing on the internet shows
exactly that.

It also costs nothing: no fixture replay to ship, no subprocess per skin, no
map to have downloaded. A hundred skins is a second, not a batch job.

## What it draws, and whose colours

The hit circle and the approach circle take the skin's own `Combo1` — osu!
tints those two and leaves the overlay and the number alone, and following that
is what makes two skins with the same shapes look as different here as they do
in the game. A skin with no `[Colours]` gets osu!'s own first default.

## Kept out of the skin's own folder

`.previews` beside the store, because `available()` skips names beginning with
a dot and `packed()` zips whatever is in a skin folder — a preview in there
would travel to every worker that ever renders in that skin.
"""

import configparser
import os
import re
from typing import Optional

from PIL import Image, ImageChops

from services.dossier.skins import folder_of, store_dir
from utils.logger import get_logger

logger = get_logger("services.dossier.preview")

# 16:9, and big enough that a phone showing two across still has real pixels.
SIZE = (512, 288)

# What osu! falls back to when a skin says nothing about colours.
DEFAULT_COMBO = (0, 202, 0)

# The circle, as a share of the canvas height. Room for the approach circle
# around it without either touching an edge.
CIRCLE_SHARE = 0.62

_BACKGROUND = (28, 26, 34, 255)


def previews_dir() -> str:
    return os.path.join(store_dir(), ".previews")


def _element(folder: str, name: str) -> Optional[Image.Image]:
    """One skin element, `@2x` preferred because this is a thumbnail.

    The high-resolution file is the better source when the result is going to
    be scaled anyway, which is the opposite of what a renderer wants and the
    reason this does not ask the engine's own loader.
    """
    for leaf in (f"{name}@2x.png", f"{name}.png"):
        path = os.path.join(folder, leaf)
        if os.path.isfile(path):
            try:
                return Image.open(path).convert("RGBA")
            except (OSError, ValueError) as exc:
                logger.warning("could not read %s: %s", path, exc)
                return None
    return None


def _ini(folder: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    path = os.path.join(folder, "skin.ini")
    if os.path.isfile(path):
        try:
            # Skins are written by hand in every encoding there is, and one
            # that will not decode is not a reason to have no preview.
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                parser.read_string(handle.read())
        except (configparser.Error, OSError) as exc:
            logger.info("skin.ini in %s did not parse: %s", folder, exc)
    return parser


def _combo_colour(folder: str) -> tuple[int, int, int]:
    """The skin's first combo colour, which is what its circles wear."""
    for section in _ini(folder).sections():
        for key, value in _ini(folder).items(section):
            if key.lower() != "combo1":
                continue
            numbers = [int(n) for n in re.findall(r"\d+", value)[:3]]
            if len(numbers) == 3 and all(0 <= n <= 255 for n in numbers):
                return tuple(numbers)  # type: ignore[return-value]
    return DEFAULT_COMBO


def _tinted(art: Image.Image, colour: tuple[int, int, int]) -> Image.Image:
    """The element in a combo colour, the way the game wears it.

    Multiplied rather than replaced, so a circle with shading in it keeps the
    shading — which is most of what tells two round skins apart.
    """
    solid = Image.new("RGBA", art.size, colour + (255,))
    lit = ImageChops.multiply(art.convert("RGBA"), solid)
    lit.putalpha(art.getchannel("A"))
    return lit


def _fitted(art: Image.Image, height: int) -> Image.Image:
    """The element at this height, measured by what is *visible* in it.

    Skins pad their elements with transparency and no two pad them the same:
    a cursor drawn as a small dot in the middle of a 128-pixel canvas and one
    cropped to its own edges are the same cursor in the game, and fitting by
    the file's height drew the first at a third of the size of the second.

    Cropping first is also what makes a grid comparable — every skin's circle
    comes out the same size, so what differs between two of them is the
    drawing rather than how much air its author left around it.
    """
    seen = art.getbbox()
    if seen is not None:
        art = art.crop(seen)
    if art.height <= 0:
        return art
    scale = height / art.height
    return art.resize(
        (max(1, round(art.width * scale)), max(1, height)), Image.LANCZOS
    )


def _paste(canvas: Image.Image, art: Optional[Image.Image], centre) -> None:
    if art is None:
        return
    canvas.alpha_composite(art, (round(centre[0] - art.width / 2),
                                 round(centre[1] - art.height / 2)))


def draw(folder: str) -> Optional[Image.Image]:
    """A picture of the skin in `folder`, or `None` if it has nothing to show.

    A skin with no hit circle gets no preview rather than a picture of our own
    fallbacks — the grid says the name instead, which is honest about there
    being nothing to see.
    """
    circle = _element(folder, "hitcircle")
    if circle is None:
        return None

    colour = _combo_colour(folder)
    canvas = Image.new("RGBA", SIZE, _BACKGROUND)
    diameter = round(SIZE[1] * CIRCLE_SHARE)
    middle = (round(SIZE[0] * 0.40), SIZE[1] // 2)

    # osu! tints the circle and the approach ring and leaves the overlay and
    # the number in their own colours. Following that is what makes two skins
    # with the same shapes look as different here as they do in the game.
    approach = _element(folder, "approachcircle")
    if approach is not None:
        _paste(canvas, _fitted(_tinted(approach, colour), round(diameter * 1.45)), middle)

    _paste(canvas, _fitted(_tinted(circle, colour), diameter), middle)

    overlay = _element(folder, "hitcircleoverlay")
    if overlay is not None:
        _paste(canvas, _fitted(overlay, diameter), middle)

    # The number a circle wears. `HitCirclePrefix` is how a skin points them
    # somewhere other than `default`.
    prefix = "default"
    for section in _ini(folder).sections():
        for key, value in _ini(folder).items(section):
            if key.lower() == "hitcircleprefix" and value.strip():
                prefix = value.strip().replace("\\", "/")
    digit = _element(folder, f"{prefix}-1")
    if digit is not None:
        _paste(canvas, _fitted(digit, round(diameter * 0.42)), middle)

    cursor = _element(folder, "cursor")
    if cursor is not None:
        _paste(
            canvas,
            _fitted(cursor, round(diameter * 0.55)),
            (round(SIZE[0] * 0.72), round(SIZE[1] * 0.66)),
        )

    return canvas


def path_of(name: str, *, rebuild: bool = False) -> Optional[str]:
    """Where this skin's preview is, drawing it if it is missing or stale.

    Stale means the skin's folder has been touched since — somebody sent the
    `.osk` again — which is rare, so this is a `stat` on the ordinary path and
    a redraw on the unusual one.
    """
    folder = folder_of(name)
    if folder is None:
        return None
    into = os.path.join(previews_dir(), f"{name}.png")

    if not rebuild and os.path.isfile(into):
        try:
            newest = max(entry.stat().st_mtime for entry in os.scandir(folder))
            if os.path.getmtime(into) >= newest:
                return into
        except (OSError, ValueError):
            pass

    picture = draw(folder)
    if picture is None:
        logger.info("skin %s has no hit circle — no preview", name)
        return None
    try:
        os.makedirs(previews_dir(), exist_ok=True)
        picture.save(into, "PNG", optimize=True)
    except OSError as exc:
        logger.warning("could not write a preview for %s: %s", name, exc)
        return None
    return into


def ensure_all(*, rebuild: bool = False) -> int:
    """Draw whatever is missing. Returns how many exist afterwards."""
    from services.dossier.skins import available

    drawn = [name for name in available() if path_of(name, rebuild=rebuild)]
    logger.info("%d skin preview(s) ready", len(drawn))
    return len(drawn)


__all__ = ["draw", "path_of", "ensure_all", "previews_dir", "SIZE"]
