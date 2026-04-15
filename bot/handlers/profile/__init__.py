from aiogram import Router

from bot.handlers.profile.handlers import router as profile_router
from bot.handlers.profile.recent import router as recent_router
from bot.handlers.profile.compare import router as compare_router

router = Router(name="profile_combined")
router.include_router(profile_router)
router.include_router(recent_router)
router.include_router(compare_router)

__all__ = ["router"]
