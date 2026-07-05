"""Admin handler package — thin shim that assembles all sub-routers.

External code imports `router` from this module; nothing else changes.
"""

from aiogram import Router

from utils.admin_check import AdminFilter

from bot.handlers.admin.panel import router as _panel_router
from bot.handlers.admin.misc import router as _misc_router
from bot.handlers.admin.gpu_watchdog import router as _gpu_watchdog_router

router = Router(name="admin")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

router.include_router(_panel_router)
router.include_router(_misc_router)
router.include_router(_gpu_watchdog_router)

