"""Bridge to Dossier, the in-house replay engine (see `dossier/`).

Re-exported on demand: this is the package the render worker imports, and
`rivals` — which draws the scoreboard's pictures — brings Pillow with it for
callers that only ever asked for `video`. See `services/__init__.py` for the
whole of why.
"""

from importlib import import_module
from typing import TYPE_CHECKING

# The name here against (module, name there). Only `collect_rivals` is renamed;
# the rest keep the name their module gave them.
_EXPORTS = {
    "MapUnavailable": ("services.dossier.maps", "MapUnavailable"),
    "describe": ("services.dossier.maps", "describe"),
    "ensure_map": ("services.dossier.maps", "ensure_map"),
    "songs_dir": ("services.dossier.maps", "songs_dir"),
    "collect_rivals": ("services.dossier.rivals", "collect"),
    "plays_here": ("services.dossier.rivals", "plays_here"),
    "ensure_pictures": ("services.dossier.rivals", "ensure_pictures"),
    "pictures_for": ("services.dossier.rivals", "pictures_for"),
    "DossierError": ("services.dossier.runner", "DossierError"),
    "Moment": ("services.dossier.runner", "Moment"),
    "Selection": ("services.dossier.runner", "Selection"),
    "exhibit": ("services.dossier.runner", "exhibit"),
    "inspect": ("services.dossier.runner", "inspect"),
    "is_available": ("services.dossier.runner", "is_available"),
    "judge": ("services.dossier.runner", "judge"),
    "moments": ("services.dossier.runner", "moments"),
    "video": ("services.dossier.runner", "video"),
}

if TYPE_CHECKING:
    from services.dossier.maps import MapUnavailable, describe, ensure_map, songs_dir
    from services.dossier.rivals import (
        collect as collect_rivals,
        ensure_pictures,
        pictures_for,
        plays_here,
    )
    from services.dossier.runner import (
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
