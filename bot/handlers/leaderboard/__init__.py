from aiogram import Router

from bot.handlers.leaderboard.handlers import router as board_router
from bot.handlers.leaderboard.modes import router as modes_router

router = Router(name="leaderboard_combined")
router.include_router(board_router)
router.include_router(modes_router)

__all__ = ["router"]
