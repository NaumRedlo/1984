"""Admin handler package — thin shim that assembles all sub-routers.

External code imports `router` from this module; nothing else changes.
"""

from aiogram import Router

from utils.admin_check import AdminFilter

from bot.handlers.admin.bounty_create import router as _bounty_create_router
from bot.handlers.admin.bounty_edit import router as _bounty_edit_router
from bot.handlers.admin.bounty_misc import router as _bounty_misc_router
from bot.handlers.admin.bsk_pool import router as _bsk_pool_router
from bot.handlers.admin.bsk_test import router as _bsk_test_router
from bot.handlers.admin.bsk_ml import router as _bsk_ml_router
from bot.handlers.admin.bsk_misc import router as _bsk_misc_router
from bot.handlers.admin.duel_force_close import router as _duel_force_close_router
from bot.handlers.admin.misc import router as _misc_router
from bot.handlers.admin.weekly import router as _weekly_router
from bot.handlers.admin.hps_pool import router as _hps_pool_router
from bot.handlers.admin.map_import import router as _map_import_router
from bot.handlers.admin.crawler import router as _crawler_router

router = Router(name="admin")
router.message.filter(AdminFilter())
router.callback_query.filter(AdminFilter())

router.include_router(_bounty_create_router)
router.include_router(_bounty_edit_router)
router.include_router(_bounty_misc_router)
router.include_router(_bsk_pool_router)
router.include_router(_bsk_test_router)
router.include_router(_bsk_ml_router)
router.include_router(_bsk_misc_router)
router.include_router(_duel_force_close_router)
router.include_router(_misc_router)
router.include_router(_weekly_router)
router.include_router(_hps_pool_router)
router.include_router(_map_import_router)
router.include_router(_crawler_router)

from bot.handlers.admin.review import (  # noqa: F401
    review_command,
    reviewselect_command,
    review_action,
)
