import configparser
import os
import shutil
from typing import Optional

from PIL import Image, ImageChops

from dossier.skins import folder_of, store_dir
from utils.logger import get_logger

logger = get_logger("services.dossier.preview")

SIZE = (512, 288)

TINT: Optional[tuple[int, int, int]] = None

CIRCLE_SHARE = 0.62

_BACKGROUND = (28, 26, 34, 255)

DRAWING = 4

def previews_dir() -> str:
    return os.path.join(store_dir(), ".previews", f"v{DRAWING}")

def forget_older() -> int:
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

            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()

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
    flat: dict[str, str] = {}
    for section in ini.sections():
        for key, value in ini.items(section):
            flat.setdefault(key.strip().lower(), value.strip())
    return flat

def _prefix(flat: dict[str, str], key: str, fallback: str) -> str:
    said = flat.get(key, "").strip()
    return said.replace("\\", "/") if said else fallback

def _tinted(art: Image.Image, colour: tuple[int, int, int]) -> Image.Image:
    solid = Image.new("RGBA", art.size, colour + (255,))
    lit = ImageChops.multiply(art.convert("RGBA"), solid)
    lit.putalpha(art.getchannel("A"))
    return lit

def _worn(art: Image.Image) -> Image.Image:
    return _tinted(art, TINT) if TINT else art

def _fitted(art: Image.Image, height: int, *, ink: int = 8) -> Image.Image:

    alpha = art.getchannel("A")
    padding = alpha.point(lambda level: 255 if level > 8 else 0).getbbox()
    if padding is not None:
        art = art.crop(padding)
        alpha = art.getchannel("A")
    if art.height <= 0:
        return art

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
    if art is None:
        return
    left = round(centre[0] - art.width / 2)
    top = round(centre[1] - art.height / 2)
    left = max(0, min(left, canvas.width - art.width))
    top = max(0, min(top, canvas.height - art.height))
    canvas.alpha_composite(art, (left, top))

def draw(folder: str) -> Optional[Image.Image]:
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

    digit = _element(folder, f'{_prefix(flat, "hitcircleprefix", "default")}-1')
    if digit is not None:
        _paste(canvas, _fitted(digit, round(diameter * 0.42)), middle)

    cursor = _element(folder, "cursor")
    if cursor is not None:

        _paste(canvas, _fitted(cursor, round(diameter * 0.30), ink=160),
               (round(SIZE[0] * 0.72), round(SIZE[1] * 0.66)))

    return canvas

def path_of(name: str, *, rebuild: bool = False) -> Optional[str]:
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

            forget_older()
        os.makedirs(previews_dir(), exist_ok=True)
        picture.save(into, "PNG", optimize=True)
    except OSError as exc:
        logger.warning("could not write a preview for %s: %s", name, exc)
        return None
    return into

def ensure_all(*, rebuild: bool = False) -> int:
    from dossier.skins import available

    drawn = [name for name in available() if path_of(name, rebuild=rebuild)]
    logger.info("%d skin preview(s) ready", len(drawn))
    return len(drawn)

__all__ = ["draw", "path_of", "ensure_all", "previews_dir", "SIZE"]
