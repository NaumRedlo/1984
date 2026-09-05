from importlib import import_module
from typing import TYPE_CHECKING

_EXPORTS = {
    "BaseCardRenderer": "services.image",
    "card_renderer": "services.image",
    "close_shared_session": "services.image",
}

if TYPE_CHECKING:
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
