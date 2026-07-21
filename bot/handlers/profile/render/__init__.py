"""Replay render subsystem.

Split into a low-level core (user_settings, cache, library, skins, pipeline) and
three handler groups, each with its own Router:

* score_handlers — the 🎬 button on rs/recent cards (`rndr:`)
* osr_handlers   — rendering an uploaded .osr file (`rdrf:` confirm flow)
* skin_handlers  — installing a custom .osk skin

The three routers are assembled here under one parent `router`. The public
helpers other modules (settings_menu) and the tests rely on are re-exported
below so `from bot.handlers.profile.render import <name>` keeps working.
"""

from aiogram import Router

from bot.handlers.profile.render import score_handlers, osr_handlers, skin_handlers

router = Router(name="render")
for _module in (score_handlers, osr_handlers, skin_handlers):
    router.include_router(_module.router)

# ── Re-exports for external importers (settings_menu) and reach-in tests ──
from bot.handlers.profile.render.user_settings import (  # noqa: E402,F401
    _get_or_create_settings, _settings_to_dict,
)
from bot.handlers.profile.render.library import (  # noqa: E402,F401
    get_user_renders, get_user_render, delete_user_render,
)
from bot.handlers.profile.render.pipeline import (  # noqa: E402,F401
    run_guarded_render, render_gate, _resolve_replay_token,
)
from bot.handlers.profile.render.skins import (  # noqa: E402,F401
    get_render_skins, get_my_render_skins, do_delete_skin, do_rename_skin,
    _add_render_skin, _remove_render_skin, _rename_render_skin_entry,
    _reassign_users_off_skin, _SKINS_KEY,
)
from bot.handlers.profile.render.skin_handlers import (  # noqa: E402,F401
    _is_public_host, _download_osk_from_url,
)
from bot.handlers.profile.render.osr_handlers import (  # noqa: E402,F401
    _confirm_render_kb, cb_confirm_render_file, prompt_render_file, _render_uploaded_osr,
)

# Class / module objects some tests patch globally — patching an attribute on the
# shared object is visible everywhere it's used, so these re-exports suffice as-is.
from osrparse import Replay  # noqa: E402,F401
from utils.osu import render_client  # noqa: E402,F401
from utils.osu.api_client import OsuApiClient  # noqa: E402,F401

__all__ = ["router"]
