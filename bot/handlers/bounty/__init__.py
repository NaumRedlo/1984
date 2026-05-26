from bot.handlers.bounty.handlers import router
from bot.handlers.bounty.nav import router as _nav_router
router.include_router(_nav_router)

__all__ = ["router"]
