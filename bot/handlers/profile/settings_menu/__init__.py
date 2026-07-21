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
    common, shell, render_settings, skins, account, titles, renders_library,
)

router = Router(name="settings")
# Callback-only guard: covers this router and every included sub-router (aiogram
# runs a parent's outer middleware before propagating to children).
router.callback_query.outer_middleware(common._owner_guard)
for _module in (shell, render_settings, skins, account, titles, renders_library):
    router.include_router(_module.router)

# Re-exported for backwards compatibility with tests that reach in via
# `from bot.handlers.profile import settings_menu as sm` and touch these names.
from bot.handlers.profile.settings_menu.common import (  # noqa: E402,F401
    _MENU_OWNERS, _home_kb, _is_foreign_menu_tap, _nav_row, _remember_owner,
)
from bot.handlers.profile.settings_menu.render_settings import (  # noqa: E402,F401
    _TOGGLES, _render_home_kb, _ui_kb, _video_kb,
)
from bot.handlers.profile.settings_menu.skins import (  # noqa: E402,F401
    _manageable_skins, _myskins_detail_kb, _myskins_kb, _resolve_my_skin, _skin_kb,
)
from bot.handlers.profile.settings_menu.account import _language_kb  # noqa: E402,F401
from bot.handlers.profile.settings_menu.renders_library import (  # noqa: E402,F401
    _broken_view, _render_detail_kb, _render_detail_text, _renders_view,
)

__all__ = ["router"]
