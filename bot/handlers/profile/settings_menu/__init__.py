from aiogram import Router

from bot.handlers.profile.settings_menu import (
    common, shell, account, titles, skin,
)

router = Router(name="settings")

router.callback_query.outer_middleware(common._owner_guard)
for _module in (shell, account, titles, skin):
    router.include_router(_module.router)

from bot.handlers.profile.settings_menu.common import (
    _MENU_OWNERS, _home_kb, _is_foreign_menu_tap, _nav_row, _remember_owner,
)
from bot.handlers.profile.settings_menu.account import _language_kb

__all__ = ["router"]
