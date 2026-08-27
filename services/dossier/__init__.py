"""The bot's side of the replay engine.

The engine and the Python that drives it are their own repository now —
[NaumRedlo/Dossier](https://github.com/NaumRedlo/Dossier), installed as
`dossier`. What is left here is the part that was never the engine's: the
scoreboard down the left of a render, the card a replay is posted with, the
skin thumbnails the mini app shows, and the replays people have agreed to share.

Names are still re-exported on demand rather than at import. `rivals` draws
pictures and brings Pillow with it, and a caller that only wanted `video`
should not pay for that — see `services/__init__.py`, same reason, same shape.

The engine's own names are re-exported too, so `from services.dossier import
video` goes on meaning what it meant. New code should say `from dossier import
runner` and mean it plainly.
"""

from importlib import import_module
from typing import TYPE_CHECKING

# The name here against (module, name there). Only `collect_rivals` is renamed;
# the rest keep the name their module gave them.
_EXPORTS = {
    # The engine's, by way of the package.
    "MapUnavailable": ("dossier.maps", "MapUnavailable"),
    "describe": ("dossier.maps", "describe"),
    "ensure_known": ("dossier.maps", "ensure_known"),
    "ensure_map": ("dossier.maps", "ensure_map"),
    "songs_dir": ("dossier.maps", "songs_dir"),
    "DossierError": ("dossier.runner", "DossierError"),
    "Moment": ("dossier.runner", "Moment"),
    "Selection": ("dossier.runner", "Selection"),
    "exhibit": ("dossier.runner", "exhibit"),
    "inspect": ("dossier.runner", "inspect"),
    "is_available": ("dossier.runner", "is_available"),
    "judge": ("dossier.runner", "judge"),
    "moments": ("dossier.runner", "moments"),
    "video": ("dossier.runner", "video"),
    # This repository's own.
    "collect_rivals": ("services.dossier.rivals", "collect"),
    "plays_here": ("services.dossier.rivals", "plays_here"),
    "ensure_pictures": ("services.dossier.rivals", "ensure_pictures"),
    "pictures_for": ("services.dossier.rivals", "pictures_for"),
}

if TYPE_CHECKING:
    from dossier.maps import (
        MapUnavailable,
        describe,
        ensure_known,
        ensure_map,
        songs_dir,
    )
    from dossier.runner import (
        DossierError,
        Moment,
        Selection,
        exhibit,
        inspect,
        is_available,
        judge,
        moments,
        video,
    )
    from services.dossier.rivals import (
        collect as collect_rivals,
        ensure_pictures,
        pictures_for,
        plays_here,
    )


def __getattr__(name: str):
    found = _EXPORTS.get(name)
    if found is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    where, called = found
    value = getattr(import_module(where), called)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = list(_EXPORTS)


def _draw_preview(name: str, rebuild: bool = False):
    """Draw a skin's thumbnail — deferred, because Pillow is."""
    from services.dossier.preview import path_of

    return path_of(name, rebuild=rebuild)


def _hand_the_engine_our_logs_and_our_pictures() -> None:
    """Tell the engine's package the two things only this side knows.

    Its logs belong in this bot's files under the bot's format, and it has no
    handlers of its own to make that awkward. And a skin that has just been
    unpacked wants a thumbnail, because there is a picker here that shows one —
    which is a fact about this repository and not about rendering, so the
    engine is told rather than assumed to know.

    Run at import of this package, which is what everything on this side of the
    seam goes through.
    """
    import logging

    from dossier import log, skins

    log.under(logging.getLogger("Bot"))
    skins.draws_previews(_draw_preview)


_hand_the_engine_our_logs_and_our_pictures()
