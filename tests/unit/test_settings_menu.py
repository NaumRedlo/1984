"""Wiring guards for the /settings inline menu (bot/handlers/profile/settings_menu).
Pure-keyboard assertions — no DB or aiogram dispatch involved."""

from bot.handlers.profile import settings_menu as sm


def _callbacks(kb):
    return {b.callback_data for row in kb.inline_keyboard for b in row}


def test_home_menu_has_all_sections():
    cbs = _callbacks(sm._home_kb())
    assert {"st:render", "st:rnd", "st:acc", "st:tt", "st:close"} <= cbs


def _fake_render(rid, label="Artist - Title"):
    import json
    from datetime import datetime
    from types import SimpleNamespace
    meta = json.dumps({"player": "p", "mods": "HDDT", "rank": "S", "pp": 200,
                       "acc": 98.5, "stars": 6.12, "version": "Extra"})
    return SimpleNamespace(id=rid, label=label, meta=meta,
                           created_at=datetime(2026, 7, 1, 12, 0))


async def test_renders_view_paginates_5_per_page(monkeypatch):
    rows = [_fake_render(i) for i in range(12)]

    async def fake_get(uid):
        return rows
    monkeypatch.setattr(sm, "get_user_renders", fake_get)

    text, kb = await sm._renders_view(uid=1, page=0)
    view_btns = [b for row in kb.inline_keyboard for b in row
                 if b.callback_data.startswith("st:rnd:v:")]
    assert len(view_btns) == 5                       # capped per page
    assert view_btns[0].callback_data == "st:rnd:v:0:0"
    cbs = _callbacks(kb)
    assert "st:rnd:pg:1" in cbs                       # forward nav (12 -> 3 pages)
    assert "st:rnd:pg:-1" not in cbs                  # no back nav on page 0


async def test_renders_view_empty(monkeypatch):
    async def fake_get(uid):
        return []
    monkeypatch.setattr(sm, "get_user_renders", fake_get)
    text, kb = await sm._renders_view(uid=1, page=0)
    assert "появятся" in text.lower()
    assert not any(b.callback_data.startswith("st:rnd:v:")
                   for row in kb.inline_keyboard for b in row)


def test_render_detail_text_shows_meta():
    txt = sm._render_detail_text(_fake_render(7, label="Camellia - GHOST"))
    assert "Camellia - GHOST" in txt
    assert "HDDT" in txt and "98.50%" in txt and "★6.12" in txt


def test_owner_guard_blocks_foreign_taps():
    sm._MENU_OWNERS.clear()
    sm._remember_owner(chat_id=10, message_id=20, tg_id=111)
    # owner taps -> allowed
    assert sm._is_foreign_menu_tap("st:render", 10, 20, 111) is False
    # bystander taps -> blocked
    assert sm._is_foreign_menu_tap("st:render", 10, 20, 999) is True
    # unknown menu (e.g. after restart) -> allowed
    assert sm._is_foreign_menu_tap("st:render", 10, 999, 999) is False
    # non-st callbacks are never guarded
    assert sm._is_foreign_menu_tap("help_osu", 10, 20, 999) is False


def _broken_render(rid, ref, beatmapset_id=None):
    import json
    from types import SimpleNamespace
    meta = json.dumps({"beatmapset_id": beatmapset_id} if beatmapset_id else {})
    return SimpleNamespace(id=rid, ref=ref, label="X - Y", meta=meta)


def test_broken_view_offers_rerender_only_for_score_entries():
    # score entry with beatmapset -> re-render offered
    _, kb = sm._broken_view(_broken_render(1, "score:42", beatmapset_id=99))
    cbs = _callbacks(kb)
    assert "st:rnd:re:1" in cbs and "st:rnd:del:1" in cbs
    # .osr upload (no replay file) -> delete only, no re-render
    _, kb2 = sm._broken_view(_broken_render(2, "osr:abc"))
    cbs2 = _callbacks(kb2)
    assert "st:rnd:re:2" not in cbs2 and "st:rnd:del:2" in cbs2


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
