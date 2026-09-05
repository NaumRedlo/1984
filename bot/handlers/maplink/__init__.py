from aiogram import Router

from bot.handlers.maplink.whatif import router as _whatif_router
from bot.handlers.maplink.handlers import router as _autodetect_router

router = Router(name="maplink_combined")
router.include_router(_whatif_router)
router.include_router(_autodetect_router)

__all__ = ["router"]
