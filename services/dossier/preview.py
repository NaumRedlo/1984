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

## What it draws

A hit circle and a cursor. A richer version — two circles, the skin's score
face, its `hit300` — was built and thrown away: at the size a grid shows these,
every extra piece made the thumbnails look *more* alike rather than less,
because the pieces crowd each other and the circle stops being the subject.

The colours are ours and fixed. A render takes its combo colours from the
*map*, not from the skin, so a preview drawn in whatever `skin.ini` declares
would show a palette that never appears in a video made with it — and it makes
the grid comparable besides, since what then differs between two thumbnails is
the art rather than what each author wrote in their `[Colours]`.

The rest of a skin is worth showing somewhere, and somewhere is a screen of its
own: see the detail view in `docs/roadmap.md`.

## Kept out of the skin's own folder

`.previews` beside the store, because `available()` skips names beginning with
a dot and `packed()` zips whatever is in a skin folder — a preview in there
would travel to every worker that ever renders in that skin.
"""

import configparser
import os
import shutil
from typing import Optional

from PIL import Image, ImageChops

from dossier.skins import folder_of, store_dir
from utils.logger import get_logger

logger = get_logger("services.dossier.preview")

# 16:9, and big enough that a phone showing two across still has real pixels.
SIZE = (512, 288)

# Whether to put a combo colour on the circle at all. Off.
#
# Three answers were tried on real skins and this is the third. Taking the
# skin's own `[Colours]` was wrong outright: a render takes its combo colours
# from the *map*, so those never appear in a video made with the skin. A fixed
# colour for everybody was the obvious replacement and was wrong too, for a
# reason that only shows on a grid — tinting *multiplies*, so the same amber
# came out amber on a white circle, olive on a cream one and dark brown on a
# grey one. The colour was one and the result was a different one per skin,
# which is exactly what a comparison grid must not do.
#
# So: no colour. The circle is shown as its author drew it. Nothing is imposed,
# nothing interacts, and what differs between two thumbnails is the drawing.
#
# `TINT` is here rather than deleted because the decision is a taste one and
# reversing it is this line plus a bump of `DRAWING`.
TINT: Optional[tuple[int, int, int]] = None

# The circle, as a share of the canvas height. Room for the approach circle
# around it without either touching an edge.
CIRCLE_SHARE = 0.62

_BACKGROUND = (28, 26, 34, 255)


# Bumped whenever `draw` changes what it puts on the picture.
#
# Previews were redrawn when the *skin* changed and never when the drawing did,
# so a change to the colours or the composition reached new skins and left
# every existing thumbnail exactly as it was. The grid went on showing pictures
# drawn by code that had been replaced — which is how a fixed palette shipped
# and the grid stayed the colour it had been.
#
# The version is a directory rather than a suffix so the old ones can be swept
# whole, and so nothing has to be parsed out of a filename to know what drew it.
DRAWING = 4


def previews_dir() -> str:
    return os.path.join(store_dir(), ".previews", f"v{DRAWING}")


def forget_older() -> int:
    """Remove previews drawn by a version that is no longer this one.

    Called when one is drawn rather than at startup: a deployment that never
    opens the grid has nothing to tidy, and one that does tidies on the way.
    """
    root = os.path.join(store_dir(), ".previews")
    gone = 0
    try:
        for entry in os.scandir(root):
            if entry.is_dir() and entry.name != f"v{DRAWING}":
                shutil.rmtree(entry.path, ignore_errors=True)
                gone += 1
    except OSError:
        return 0
    if gone:
        logger.info("swept %d directory(ies) of previews drawn by older code", gone)
    return gone


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
                text = handle.read()
            # `//` is not a comment to `configparser` and is a comment to every
            # skin author who has ever written one. A file full of them threw
            # a parsing error per line into the log and lost the whole file
            # with it — including `HitCirclePrefix`, which is how a skin says
            # where its digits live.
            parser.read_string(
                "\n".join(
                    line for line in text.splitlines()
                    if not line.lstrip().startswith("//")
                )
            )
        except (configparser.Error, OSError) as exc:
            logger.info("skin.ini in %s did not parse: %s", folder, exc)
    return parser


def _settings(ini: configparser.ConfigParser) -> dict[str, str]:
    """Every key in the file, flattened and lowercased.

    Skins put the same key under whatever heading they please, and some ship
    two `[Colours]` sections. What matters is the key, not where it sat.
    """
    flat: dict[str, str] = {}
    for section in ini.sections():
        for key, value in ini.items(section):
            flat.setdefault(key.strip().lower(), value.strip())
    return flat


def _prefix(flat: dict[str, str], key: str, fallback: str) -> str:
    """Where a skin points one of its two number faces.

    `HitCirclePrefix` and `ScorePrefix` are how a skin says its digits live
    somewhere other than the default, and a skin that uses them and is read
    without them comes out with no numbers at all.
    """
    said = flat.get(key, "").strip()
    return said.replace("\\", "/") if said else fallback


def _tinted(art: Image.Image, colour: tuple[int, int, int]) -> Image.Image:
    """The element in a combo colour, the way the game wears it.

    Multiplied rather than replaced, so a circle with shading in it keeps the
    shading — which is most of what tells two round skins apart.
    """
    solid = Image.new("RGBA", art.size, colour + (255,))
    lit = ImageChops.multiply(art.convert("RGBA"), solid)
    lit.putalpha(art.getchannel("A"))
    return lit


def _worn(art: Image.Image) -> Image.Image:
    """The element as it goes on the picture: tinted, or exactly as drawn."""
    return _tinted(art, TINT) if TINT else art


def _fitted(art: Image.Image, height: int, *, ink: int = 8) -> Image.Image:
    """The element at this height, measured by what is *visible* in it.

    Skins pad their elements with transparency and no two pad them the same:
    a cursor drawn as a small dot in the middle of a 128-pixel canvas and one
    cropped to its own edges are the same cursor in the game, and fitting by
    the file's height drew the first at a third of the size of the second.

    Cropping first is also what makes a grid comparable — every skin's circle
    comes out the same size, so what differs between two of them is the
    drawing rather than how much air its author left around it.
    """
    # Two thresholds, because "how big is this element" has two answers and
    # only one of them is right per element.
    #
    # The low one strips true padding: a skin whose circle sits in a canvas of
    # almost-but-not-quite-transparent pixels cropped to nothing at `getbbox()`,
    # which counts a single unit of alpha as ink.
    alpha = art.getchannel("A")
    padding = alpha.point(lambda level: 255 if level > 8 else 0).getbbox()
    if padding is not None:
        art = art.crop(padding)
        alpha = art.getchannel("A")
    if art.height <= 0:
        return art

    # And `ink` is what the element *is*, as opposed to what it glows. Cursors
    # are drawn as a bright dot inside a soft halo and no two authors agree on
    # how far the halo goes: measured across the skins in hand the dot is
    # anywhere from a third of the file to three quarters of it, so sizing by
    # the whole thing made the visible dot vary by more than double between two
    # skins whose cursors are the same size in the game.
    #
    # So the *core* is what reaches `height`, and the halo scales with it and
    # spills past — which is what a glow does.
    measured = art.height
    if ink > 8:
        core = alpha.point(lambda level: 255 if level > ink else 0).getbbox()
        if core is not None and core[3] - core[1] > 0:
            measured = core[3] - core[1]

    scale = height / measured
    return art.resize(
        (max(1, round(art.width * scale)), max(1, round(art.height * scale))),
        Image.LANCZOS,
    )


def _paste(canvas: Image.Image, art: Optional[Image.Image], centre) -> None:
    """Put `art` down centred on `centre`, kept inside the picture.

    Clamped rather than trusted. Elements differ in width by a factor of four
    between skins — a `hit300` that is a wide word and one that is a small
    badge — so a placement that fits one runs off the edge of another, and a
    clipped mark reads as a broken preview rather than as a big one.
    """
    if art is None:
        return
    left = round(centre[0] - art.width / 2)
    top = round(centre[1] - art.height / 2)
    left = max(0, min(left, canvas.width - art.width))
    top = max(0, min(top, canvas.height - art.height))
    canvas.alpha_composite(art, (left, top))


def draw(folder: str) -> Optional[Image.Image]:
    """A picture of the skin in `folder`, or `None` if it has nothing to show.

    A hit circle and a cursor, and deliberately nothing else. A version with
    two circles, the skin's score face and its `hit300` was tried and is not
    what a thumbnail wants: at the size a grid shows these, the extra pieces
    are clutter that makes every skin look like every other skin. The circle is
    what a person recognises, and it is what the picture is of.

    The rest of a skin is worth showing somewhere, and somewhere is a screen of
    its own — see `docs/roadmap.md`.

    A skin with no hit circle gets no preview rather than a picture of our own
    fallbacks. The grid says its name instead, which is honest about there
    being nothing to see.
    """
    circle = _element(folder, "hitcircle")
    if circle is None:
        return None

    flat = _settings(_ini(folder))
    canvas = Image.new("RGBA", SIZE, _BACKGROUND)
    diameter = round(SIZE[1] * CIRCLE_SHARE)
    middle = (round(SIZE[0] * 0.40), SIZE[1] // 2)

    approach = _element(folder, "approachcircle")
    if approach is not None:
        _paste(canvas, _fitted(_worn(approach), round(diameter * 1.45)), middle)

    _paste(canvas, _fitted(_worn(circle), diameter), middle)

    overlay = _element(folder, "hitcircleoverlay")
    if overlay is not None:
        _paste(canvas, _fitted(overlay, diameter), middle)

    # The number a circle wears. `HitCirclePrefix` is how a skin says its
    # digits live somewhere other than `default`, and one that does and is read
    # without it comes out with no number at all.
    digit = _element(folder, f'{_prefix(flat, "hitcircleprefix", "default")}-1')
    if digit is not None:
        _paste(canvas, _fitted(digit, round(diameter * 0.42)), middle)

    cursor = _element(folder, "cursor")
    if cursor is not None:
        # Measured by its bright core rather than by how far it glows.
        _paste(canvas, _fitted(cursor, round(diameter * 0.30), ink=160),
               (round(SIZE[0] * 0.72), round(SIZE[1] * 0.66)))

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
        if not os.path.isdir(previews_dir()):
            # The first drawing at this version, which is the moment the last
            # version stopped being wanted.
            forget_older()
        os.makedirs(previews_dir(), exist_ok=True)
        picture.save(into, "PNG", optimize=True)
    except OSError as exc:
        logger.warning("could not write a preview for %s: %s", name, exc)
        return None
    return into


def ensure_all(*, rebuild: bool = False) -> int:
    """Draw whatever is missing. Returns how many exist afterwards."""
    from dossier.skins import available

    drawn = [name for name in available() if path_of(name, rebuild=rebuild)]
    logger.info("%d skin preview(s) ready", len(drawn))
    return len(drawn)


__all__ = ["draw", "path_of", "ensure_all", "previews_dir", "SIZE"]
