from aiogram import Router

from bot.handlers.duel.cancel import router as cancel_router
from bot.handlers.duel.duel import router as duel_router
from bot.handlers.duel.history import router as history_router
from bot.handlers.duel.profile_panel import router as profile_panel_router
from bot.handlers.duel.stats import router as stats_router
from bot.handlers.duel.status import router as status_router

router = Router(name="duel")

router.include_routers(
    profile_panel_router,
    duel_router,
    status_router,
    cancel_router,
    stats_router,
    history_router,
)
