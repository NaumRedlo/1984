"""Wiring guards for the /settings inline menu (bot/handlers/profile/settings_menu).
Pure-keyboard assertions — no DB or aiogram dispatch involved."""

from bot.handlers.profile import settings_menu as sm


def _callbacks(kb):
    return {b.callback_data for row in kb.inline_keyboard for b in row}


def test_home_menu_has_all_sections():
    cbs = _callbacks(sm._home_kb())
    assert {"st:acc", "st:tt", "st:lang", "st:close"} <= cbs


def test_owner_guard_blocks_foreign_taps():
    sm._MENU_OWNERS.clear()
    sm._remember_owner(chat_id=10, message_id=20, tg_id=111)
    # owner taps -> allowed
    assert sm._is_foreign_menu_tap("st:acc", 10, 20, 111) is False
    # bystander taps -> blocked
    assert sm._is_foreign_menu_tap("st:acc", 10, 20, 999) is True
    # unknown menu (e.g. after restart) -> allowed
    assert sm._is_foreign_menu_tap("st:acc", 10, 999, 999) is False
    # non-st callbacks are never guarded
    assert sm._is_foreign_menu_tap("help_osu", 10, 20, 999) is False


def test_nav_row_back_and_close():
    cbs = {b.callback_data for b in sm._nav_row()}
    assert cbs == {"st:home", "st:close"}


def test_language_kb_marks_current():
    kb = sm._language_kb("RU")
    cbs = _callbacks(kb)
    assert {"st:lang:set:EN", "st:lang:set:RU", "st:home", "st:close"} <= cbs
    texts = {b.text for row in kb.inline_keyboard for b in row}
    assert any(t.startswith("●") and "Русский" in t for t in texts)
    assert not any(t.startswith("●") and "English" in t for t in texts)
