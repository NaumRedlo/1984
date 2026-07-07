from aiogram import Router

from bot.handlers.maplink.whatif import router as _whatif_router
from bot.handlers.maplink.handlers import router as _autodetect_router

# Explicit "map <accuracy> [mods]" command first, passive link auto-detect
# second — a plain priority convention (explicit commands never carry a raw
# link of their own anymore, so there's no real filter overlap left, but an
# explicit command should still win if that ever changes).
router = Router(name="maplink_combined")
router.include_router(_whatif_router)
router.include_router(_autodetect_router)

__all__ = ["router"]
