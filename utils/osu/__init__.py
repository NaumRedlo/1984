"""osu! helpers, re-exported on demand rather than on import.

Importing this used to bring in the API client and, with it, SQLAlchemy and
the database — for callers that only wanted `extract_beatmap_id`. The render
worker was one of them, by way of `services.dossier.maps`.

See `services/__init__.py`: same reason, same shape.
"""

from importlib import import_module
from typing import TYPE_CHECKING

_EXPORTS = {
    "OsuApiClient": "utils.osu.api_client",
    "extract_beatmap_id": "utils.osu.helpers",
    "get_community_stats": "utils.osu.helpers",
    "get_message_context": "utils.osu.helpers",
    "remember_message_context": "utils.osu.helpers",
    "OsuUserLookupError": "utils.osu.resolve_user",
    "OsuUserNotFoundError": "utils.osu.resolve_user",
    "get_any_user_by_telegram_id": "utils.osu.resolve_user",
    "get_registered_user": "utils.osu.resolve_user",
    "get_registered_user_by_osu": "utils.osu.resolve_user",
    "resolve_osu_query_status": "utils.osu.resolve_user",
    "resolve_osu_user": "utils.osu.resolve_user",
    "resolve_registered_user": "utils.osu.resolve_user",
}

if TYPE_CHECKING:
    from utils.osu.api_client import OsuApiClient
    from utils.osu.helpers import (
        extract_beatmap_id,
        get_community_stats,
        get_message_context,
        remember_message_context,
    )
    from utils.osu.resolve_user import (
        OsuUserLookupError,
        OsuUserNotFoundError,
        get_any_user_by_telegram_id,
        get_registered_user,
        get_registered_user_by_osu,
        resolve_osu_query_status,
        resolve_osu_user,
        resolve_registered_user,
    )


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
