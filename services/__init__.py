"""The bot's services, re-exported on demand rather than on import.

This used to pull the card renderer the moment anything under `services` was
imported, and `services.dossier` is imported by the render worker. So a
machine whose whole job is to run a native binary and hand back an `.mp4` was
loading Pillow, fontTools, the pp calculator and — through `utils.osu` — the
API client, SQLAlchemy and the database layer. None of it is touched by a
render.

That is a real cost rather than a tidiness one: a worker runs on somebody
else's laptop, and what it imports is what they have to install.

Names still resolve exactly as before; they are just fetched the first time
they are asked for and cached as ordinary globals afterwards.
"""

from importlib import import_module
from typing import TYPE_CHECKING

_EXPORTS = {
    "BaseCardRenderer": "services.image",
    "card_renderer": "services.image",
    "close_shared_session": "services.image",
}

if TYPE_CHECKING:  # so editors and type checkers still see the real thing
    from services.image import BaseCardRenderer, card_renderer, close_shared_session


def __getattr__(name: str):
    where = _EXPORTS.get(name)
    if where is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(where), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))


__all__ = list(_EXPORTS)
