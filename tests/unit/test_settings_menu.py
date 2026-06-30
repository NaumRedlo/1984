"""Wiring guards for the /settings inline menu (bot/handlers/profile/settings_menu).
Pure-keyboard assertions — no DB or aiogram dispatch involved."""

from bot.handlers.profile import settings_menu as sm


def _callbacks(kb):
    return {b.callback_data for row in kb.inline_keyboard for b in row}


def test_home_menu_has_all_sections():
    cbs = _callbacks(sm._home_kb())
    assert {"st:render", "st:acc", "st:tt", "st:close"} <= cbs


def test_nav_row_back_and_close():
    cbs = {b.callback_data for b in sm._nav_row()}
    assert cbs == {"st:home", "st:close"}


def test_hitsounds_toggle_registered():
    # The skin-hitsounds toggle drives UserRenderSettings.use_skin_hitsounds.
    assert sm._TOGGLES["hs"][0] == "use_skin_hitsounds"
