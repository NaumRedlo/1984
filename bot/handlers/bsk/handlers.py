from aiogram import Router

from bot.handlers.bsk.ban import router as ban_router
from bot.handlers.bsk.cancel import router as cancel_router
from bot.handlers.bsk.duel import router as duel_router
from bot.handlers.bsk.history import router as history_router
from bot.handlers.bsk.match import router as match_router
from bot.handlers.bsk.pause import router as pause_router
from bot.handlers.bsk.pick import router as pick_router
from bot.handlers.bsk.profile_panel import router as profile_panel_router
from bot.handlers.bsk.stats import router as stats_router
from bot.handlers.bsk.status import router as status_router

router = Router(name="bsk")

router.include_routers(
    profile_panel_router,
    duel_router,
    status_router,
    cancel_router,
    stats_router,
    pick_router,
    ban_router,
    pause_router,
    history_router,
    match_router,
)
