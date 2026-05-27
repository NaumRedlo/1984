from bot.handlers.bounty.handlers import router
from bot.handlers.bounty.nav import router as _nav_router
from bot.handlers.bounty.replay import router as _replay_router
router.include_router(_nav_router)
router.include_router(_replay_router)

__all__ = ["router"]
