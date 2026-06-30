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


def _fake_render_settings():
    from types import SimpleNamespace
    ns = SimpleNamespace(skin="default", resolution="1920x1080", bg_dim=80, cursor_size=1.0)
    for field, _ in sm._TOGGLES.values():
        setattr(ns, field, True)
    return ns


def test_render_home_has_categories_and_reset():
    cbs = _callbacks(sm._render_home_kb())
    assert {"st:rvideo", "st:rui", "st:rreset", "st:home", "st:close"} <= cbs


def test_video_screen_has_cyclers_hitsounds_and_back():
    cbs = _callbacks(sm._video_kb(_fake_render_settings()))
    assert {"st:rc:skin", "st:rc:res", "st:rc:dim", "st:rc:cur", "st:rt:hs"} <= cbs
    assert {"st:render", "st:close"} <= cbs   # back row points to the render home


def test_ui_screen_has_hud_toggles_only():
    cbs = _callbacks(sm._ui_kb(_fake_render_settings()))
    # HUD toggles present, hitsounds (video screen) absent.
    assert "st:rt:pp" in cbs and "st:rt:sw" in cbs
    assert "st:rt:hs" not in cbs
    assert {"st:render", "st:close"} <= cbs
