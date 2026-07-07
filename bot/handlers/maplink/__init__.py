from aiogram import Router

from bot.handlers.maplink.whatif import router as _whatif_router
from bot.handlers.maplink.handlers import router as _autodetect_router

# Explicit "map ..." command first: a message like "map <link> 94 hr" carries
# a real beatmap link and doesn't start with "/", so it would also satisfy
# the passive auto-detect filter below if that ran first.
router = Router(name="maplink_combined")
router.include_router(_whatif_router)
router.include_router(_autodetect_router)

__all__ = ["router"]
