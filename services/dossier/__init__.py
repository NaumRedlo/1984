"""Bridge to Dossier, the in-house replay engine (see `dossier/`)."""

from services.dossier.maps import MapUnavailable, describe, ensure_map, songs_dir
from services.dossier.rivals import (
    collect as collect_rivals,
    ensure_pictures,
    pictures_for,
)
from services.dossier.runner import (
    DossierError,
    Moment,
    exhibit,
    inspect,
    is_available,
    judge,
    moments,
    video,
)

__all__ = [
    "collect_rivals",
    "ensure_pictures",
    "pictures_for",
    "DossierError",
    "Moment",
    "exhibit",
    "MapUnavailable",
    "describe",
    "ensure_map",
    "inspect",
    "is_available",
    "judge",
    "moments",
    "songs_dir",
    "video",
]
