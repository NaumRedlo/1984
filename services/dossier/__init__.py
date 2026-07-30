"""Bridge to Dossier, the in-house replay engine (see `dossier/`)."""

from services.dossier.maps import MapUnavailable, describe, ensure_map, songs_dir
from services.dossier.rivals import collect as collect_rivals, pictures_for
from services.dossier.runner import DossierError, inspect, is_available, judge, video

__all__ = [
    "collect_rivals",
    "pictures_for",
    "DossierError",
    "MapUnavailable",
    "describe",
    "ensure_map",
    "inspect",
    "is_available",
    "judge",
    "songs_dir",
    "video",
]
