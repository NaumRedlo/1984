"""Unified bot settings command (`sts`).

An inline-keyboard menu, split into one submodule per section (each with its own
Router), assembled here under a single parent router. `_owner_guard` is
registered on the parent so it — and the `lang` it injects — covers every
section's callbacks. Add a future section by adding a button on the home menu
(`common._home_kb`), a new `st:<section>` submodule, and including its router
below.
"""

from aiogram import Router

from bot.handlers.profile.settings_menu import (
    common, shell, account, titles, render, effects, sound, skins,
)

router = Router(name="settings")
# Callback-only guard: covers this router and every included sub-router (aiogram
# runs a parent's outer middleware before propagating to children).
router.callback_query.outer_middleware(common._owner_guard)
for _module in (shell, account, titles, render, effects, sound, skins):
    router.include_router(_module.router)

# Re-exported for backwards compatibility with tests that reach in via
# `from bot.handlers.profile import settings_menu as sm` and touch these names.
from bot.handlers.profile.settings_menu.common import (  # noqa: E402,F401
    _MENU_OWNERS, _home_kb, _is_foreign_menu_tap, _nav_row, _remember_owner,
)
from bot.handlers.profile.settings_menu.account import _language_kb  # noqa: E402,F401

__all__ = ["router"]
