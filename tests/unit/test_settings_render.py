"""The render section of `sts`, and the one thing on it that is not a taste.

Size, frame rate and sound are preferences. "Send replay data to the developer"
is permission to take somebody's files, and the tests that matter here are the
ones about it being off unless it was asked for.
"""

import pytest

from bot.handlers.dossier.renders import Choices
from bot.handlers.profile.settings_menu import render as section
from bot.handlers.profile.settings_menu import skins as skin_tab
from utils.i18n import t


def buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


# ── the preferences ───────────────────────────────────────────────────────

def test_every_option_offered_is_one_the_menu_will_accept():
    """The keyboard and the handler read the same table, so a button can never
    lead to "no such setting" — which is what a second list would eventually
    produce."""
    chosen = Choices()
    for key, rows in section.OPTIONS.items():
        for value, _ in [pair for row in rows for pair in row]:
            assert section._apply(chosen, key, value), f"{key}={value}"
    for key in section.TOGGLES:
        for value in ("0", "1"):
            assert section._apply(chosen, key, value), f"{key}={value}"


def test_a_value_the_menu_never_offered_is_refused():
    """Callback data is user input, and an old keyboard outlives the option it
    was drawn for."""
    chosen = Choices()
    assert not section._apply(chosen, "size", "4000x3000")
    assert not section._apply(chosen, "nonsense", "1")
    assert chosen.size == Choices().size, "and nothing was changed on the way"


def test_what_is_already_true_is_marked_rather_than_hidden():
    chosen = Choices(size="854x480", fps=30)
    marked = [b.text for b in buttons(section._quality_kb(chosen, "en"))
              if b.text.startswith("● ")]
    assert marked == ["● 480p", "● 30 fps"]


def test_a_switch_shows_what_it_is_rather_than_offering_both_halves():
    """One button apiece, ticked when the thing named is on. Two buttons for a
    yes/no is twice the width to say the same thing.

    Each switch is now on the sub-tab it belongs to — silence with the levels,
    the two about what the frame contains with what the frame is drawn at — so
    they are gathered from both to be checked.
    """
    from bot.handlers.profile.settings_menu import sound

    on = Choices(mute=True, background=True, bare=False)
    switches = {
        b.callback_data.rsplit(":", 2)[1]: b.text
        for kb in (section._quality_kb(on, "ru"), sound._kb(on, "ru"))
        for b in buttons(kb)
        if b.callback_data and b.callback_data.count(":") == 3
        and b.callback_data.split(":")[2] in section.TOGGLES
    }
    assert switches["mute"].startswith("☑️")
    assert switches["background"].startswith("☑️")
    assert switches["bare"].startswith("⬜️")


def test_each_switch_is_drawn_in_exactly_one_place():
    """Which is what lets a tap redraw the right screen without carrying it in
    a callback that is already four parts long."""
    from bot.handlers.profile.settings_menu import sound

    chosen = Choices()
    seen: dict[str, int] = {}
    for kb in (
        section._render_kb(chosen, False, "ru"),
        section._quality_kb(chosen, "ru"),
        sound._kb(chosen, "ru"),
    ):
        for b in buttons(kb):
            data = b.callback_data or ""
            if data.count(":") == 3 and data.split(":")[2] in section.TOGGLES:
                seen[data.split(":")[2]] = seen.get(data.split(":")[2], 0) + 1
    assert seen == {key: 1 for key in section.TOGGLES}, seen


def test_4k_and_120_are_offered():
    values = {v for row in section.OPTIONS["size"] for v, _ in row}
    assert "3840x2160" in values and "2560x1440" in values
    assert "120" in {v for row in section.OPTIONS["fps"] for v, _ in row}


def test_the_resolutions_are_laid_out_over_more_than_one_row():
    """Five of them in one row is five unreadable slivers. The rows are part of
    the table because they are a property of the values."""
    assert len(section.OPTIONS["size"]) > 1
    assert all(len(row) <= 3 for row in section.OPTIONS["size"])


def test_the_sound_setting_survives_the_round_trip_through_a_callback():
    # Stored as a bool and carried as "0"/"1"; the two have disagreed before.
    chosen = Choices()
    assert section._apply(chosen, "mute", "1") and chosen.mute is True
    assert section._current(chosen, "mute") == "1"
    assert section._apply(chosen, "mute", "0") and chosen.mute is False
    assert section._current(chosen, "mute") == "0"


# ── the permission ────────────────────────────────────────────────────────

def test_sharing_is_off_until_it_is_switched_on():
    """The whole point. A consent box that starts ticked is not consent, and a
    default of "on" here would mean the bot took files from everybody who never
    opened this screen."""
    box = [b for b in buttons(section._render_kb(Choices(), False, "en"))
           if "share" in (b.callback_data or "")]
    assert len(box) == 1
    assert box[0].text.startswith("⬜️"), box[0].text
    assert box[0].callback_data.endswith(":1"), "and tapping it turns it on"


def test_the_switch_offers_the_opposite_of_what_is_set():
    on = [b for b in buttons(section._render_kb(Choices(), True, "en"))
          if "share" in (b.callback_data or "")][0]
    assert on.text.startswith("☑️")
    assert on.callback_data.endswith(":0"), "tapping a ticked box unticks it"


def test_the_label_says_what_happens_rather_than_how_it_feels():
    """Somebody reads this once, quickly. They have to come away knowing a file
    leaves their hands."""
    for lang in ("en", "ru"):
        label = t("sts.rnd.share", lang)
        assert label and label != "sts.rnd.share", lang
    assert "replay" in t("sts.rnd.share", "en").lower()
    assert "реплея" in t("sts.rnd.share", "ru").lower()


def test_turning_it_on_spells_out_what_is_sent():
    """Not "sharing enabled". The `.osr` and the engine's reading of it are two
    different things to hand over, and both are named."""
    for lang in ("en", "ru"):
        said = t("sts.rnd.share_on", lang).lower()
        assert ".osr" in said, lang
        assert "off" in said or "выключить" in said, "and how to stop"


def test_the_screen_keeps_saying_so_while_it_is_on():
    """The explanation belongs where somebody looks months later wondering what
    the bot has of theirs — not only in the moment they agreed."""
    body = section._text(Choices(), sharing=True, lang="ru")
    assert t("sts.rnd.share_on", "ru") in body
    assert t("sts.rnd.share_on", "ru") not in section._text(Choices(), False, "ru")


@pytest.mark.parametrize("lang", ["en", "ru"])
def test_the_section_speaks_both_languages(lang):
    keys = ["sts.kb.render", "sts.rnd.body", "sts.rnd.mute", "sts.rnd.background",
            "sts.rnd.bare", "sts.rnd.ration", "sts.rnd.ration_spent",
            "sts.rnd.ration_needs_account", "sts.rnd.share", "sts.rnd.share_off",
            "sts.rnd.share_needs_account", "sts.rnd.unknown"]
    missing = [k for k in keys if t(k, lang) == k]
    assert not missing, missing


# ── choosing a skin ───────────────────────────────────────────────────────

def test_the_engines_own_look_is_always_offered_and_is_the_default(monkeypatch):
    """Whatever is in the store, there is always something to fall back to —
    and it is what a fresh account renders in."""
    monkeypatch.setattr(skin_tab.store, "available", lambda: [])
    rows = skin_tab.rows(Choices(), "en")
    assert len(rows) == 1
    assert rows[0][0].text.startswith("● "), "and it is the one marked"
    assert rows[0][0].callback_data.endswith(f":{skin_tab.DEFAULT_SKIN}")


def test_every_stored_skin_gets_a_button(monkeypatch):
    monkeypatch.setattr(skin_tab.store, "available", lambda: ["doki", "rafis"])
    names = [b.callback_data.split(":", 3)[3]
             for row in skin_tab.rows(Choices(), "en") for b in row]
    assert names == [skin_tab.DEFAULT_SKIN, "doki", "rafis"]


def test_skins_go_three_to_a_row(monkeypatch):
    """One per row turned a screen with a handful of skins into a scroll."""
    monkeypatch.setattr(skin_tab.store, "available", lambda: [f"s{n}" for n in range(7)])
    rows = skin_tab.rows(Choices(), "en")
    assert [len(row) for row in rows] == [3, 3, 2]


def test_the_chosen_skin_is_the_marked_one(monkeypatch):
    monkeypatch.setattr(skin_tab.store, "available", lambda: ["doki", "rafis"])
    marked = [b.text for row in skin_tab.rows(Choices(skin="rafis"), "en")
              for b in row if b.text.startswith("● ")]
    assert marked == ["● rafis"]


def test_a_skin_arrives_by_being_sent_rather_than_typed():
    """The label has to say so: nothing else in the bot tells you how a skin
    gets into the list, and a list you cannot add to reads as broken."""
    for lang in ("en", "ru"):
        assert ".osk" in t("sts.rnd.skin", lang), lang


def test_nothing_shown_as_a_popup_is_longer_than_telegram_allows():
    """Reported from the bot: tapping the consent box threw an error and the
    log said the text was too long. `answerCallbackQuery` refuses anything over
    200 characters outright, and the full wording of the consent runs to 224 —
    so the popup says the short version and the screen keeps the long one.

    Checked for every string that reaches a popup rather than for the one that
    broke, since the next one added would break the same way.
    """
    popups = [
        "sts.rnd.share_agreed",
        "sts.rnd.share_off",
        "sts.rnd.share_needs_account",
        "sts.rnd.skin_gone",
        "sts.rnd.unknown",
    ]
    for key in popups:
        for lang in ("en", "ru"):
            said = t(key, lang)
            assert len(said) <= 200, f"{key}/{lang} is {len(said)} characters"


def test_the_long_wording_is_still_on_the_screen():
    """Shortening the popup must not shorten the explanation: the screen is
    where somebody reads it months later."""
    body = section._text(Choices(), sharing=True, lang="ru")
    assert ".osr" in body and len(t("sts.rnd.share_on", "ru")) > 200


# ── remembering ───────────────────────────────────────────────────────────

class Row:
    """Just the columns the settings live in."""

    def __init__(self, **kw):
        self.render_size = kw.get("render_size")
        self.render_fps = kw.get("render_fps")
        self.render_mute = kw.get("render_mute")
        self.render_skin = kw.get("render_skin")
        self.render_background = kw.get("render_background")
        self.render_bare = kw.get("render_bare")
        self.heavy_renders = kw.get("heavy_renders")
        self.heavy_renders_on = kw.get("heavy_renders_on")


def test_settings_survive_a_restart():
    """They used to live only in memory, which was fine while the worst a
    restart cost was re-picking a resolution — a skin is chosen from a list
    somebody had to send the bot first, and losing that is losing work."""
    from bot.handlers.dossier import renders

    chosen = Choices(size="1920x1080", fps=30, mute=True, skin="doki")
    row = Row()
    renders.remember_settings(row, chosen)

    after = renders.restore_settings(row, Choices())
    assert (after.size, after.fps, after.mute, after.skin) == (
        "1920x1080", 30, True, "doki",
    )


def test_an_account_that_never_chose_anything_keeps_the_defaults():
    """Four nulls mean "as it comes", not "off" — read as booleans they would
    mute every render for everyone who never opened the screen."""
    from bot.handlers.dossier import renders

    fresh = Choices()
    after = renders.restore_settings(Row(), Choices())
    assert (after.size, after.fps, after.mute) == (fresh.size, fresh.fps, fresh.mute)


def test_a_render_without_an_account_still_has_settings():
    """Nobody to read from is not an error: the in-memory choices stand."""
    from bot.handlers.dossier import renders

    chosen = Choices(size="854x480")
    assert renders.restore_settings(None, chosen) is chosen


# ── the ration on 4K and 120 fps ──────────────────────────────────────────

def test_only_what_is_past_the_old_ceiling_is_rationed():
    """A ration on renders in general would be a ration on something that was
    always cheap. What costs a machine minutes rather than seconds is the part
    that arrived with 4K."""
    assert not Choices(size="1920x1080", fps=60).heavy()
    assert not Choices(size="854x480", fps=30).heavy()
    assert Choices(size="1920x1080", fps=120).heavy()
    assert Choices(size="2560x1440", fps=60).heavy()
    assert Choices(size="3840x2160", fps=120).heavy()


def test_a_size_that_cannot_be_read_is_not_treated_as_expensive():
    """A stale keyboard or a hand-made callback should not be able to make
    something rationed by being unparseable."""
    assert not Choices(size="nonsense").heavy()


def test_the_ration_is_counted_per_day_and_rolls_over():
    from bot.handlers.dossier import renders

    row = Row()
    assert renders.heavy_left(row) == renders.HEAVY_PER_DAY
    for _ in range(renders.HEAVY_PER_DAY):
        renders.spend_heavy(row)
    assert renders.heavy_left(row) == 0
    # Yesterday's count is not today's.
    row.heavy_renders_on = "2000-01-01"
    assert renders.heavy_left(row) == renders.HEAVY_PER_DAY


def test_an_account_that_does_not_exist_has_no_ration():
    """The count has to be written down somewhere, and an unlinked account has
    nowhere. Said on the settings screen rather than sprung on somebody holding
    a replay."""
    from bot.handlers.dossier import renders

    assert renders.heavy_left(None) == 0


def test_the_screen_says_what_is_left_before_anybody_asks():
    """Somebody picking 4K should know what it costs then, not when they go
    looking for a video.

    Said on the quality sub-tab, which is where 4K is chosen. On the render
    screen it was a sentence about a ration beside no way to spend it.
    """
    body = section._quality_text(Choices(), lang="ru", left=3)
    assert "3" in body and "5" in body


def test_the_two_new_switches_survive_the_round_trip():
    chosen = Choices()
    for key in ("background", "bare"):
        assert section._apply(chosen, key, "1") and getattr(chosen, key) is True
        assert section._current(chosen, key) == "1"
        assert section._apply(chosen, key, "0") and getattr(chosen, key) is False


def test_a_switch_refuses_anything_that_is_not_on_or_off():
    chosen = Choices()
    assert not section._apply(chosen, "bare", "maybe")
    assert chosen.bare is False


def test_the_new_settings_survive_a_restart():
    from bot.handlers.dossier import renders

    chosen = Choices(background=True, bare=True)
    row = Row()
    renders.remember_settings(row, chosen)
    after = renders.restore_settings(row, Choices())
    assert (after.background, after.bare) == (True, True)


def test_the_summary_names_only_what_is_switched_on():
    """It is read at a glance from the render's own status line, so a list of
    everything that is off is noise."""
    assert "фон" not in Choices().summary()
    assert "фон" in Choices(background=True).summary()
    assert "без интерфейса" in Choices(bare=True).summary()


# ── the render sub-tabs (`st:fx`) ────────────────────────────────────────────


def test_every_switch_belongs_to_a_group_that_exists():
    from bot.handlers.profile.settings_menu import effects

    for name, group, _ in effects.SWITCHES:
        assert group in effects.GROUPS, f"{name} is filed under a group nobody shows"
    # And no group is empty — an ordering with a hole in it.
    for group in effects.GROUPS:
        assert any(belongs == group for _, belongs, _ in effects.SWITCHES), group


def test_the_bot_and_the_engine_agree_on_which_movements_exist():
    """The engine is the authority on both the names and the defaults.

    They are repeated in the bot because a settings screen has to draw the boxes
    before anything is stored, and a repeat that nobody checks is a repeat that
    drifts — the day the engine turns one on by default, the menu would keep
    showing it as off and switch it off on the first tap.
    """
    import pathlib
    import re

    from bot.handlers.profile.settings_menu import effects

    source = pathlib.Path("dossier/crates/dossier-render/src/skin.rs").read_text()
    listed = re.search(r"ALL: \[&'static str; \d+\] = \[(.*?)\];", source, re.S)
    assert listed, "the engine's list of movements moved — find it and fix this"
    names = re.findall(r'"([a-z-]+)"', listed.group(1))
    assert names == [name for name, _, _ in effects.SWITCHES]

    # `apply` sets every flag from the list, so the defaults live on `Skin`
    # itself: `cursor_trail: true` and the rest false.
    for name, _, default in effects.SWITCHES:
        field = name.replace("-", "_")
        found = re.search(rf"^\s+{field}: (true|false),", source, re.M)
        assert found, f"the engine has no default for {name}"
        assert (found.group(1) == "true") is default, name


def test_a_person_who_never_opened_the_sub_tabs_gets_the_engines_defaults():
    from bot.handlers.profile.settings_menu import effects

    fresh = Choices()
    assert fresh.effects is None, "nothing is stored until something is chosen"
    assert effects._on(fresh) == {"cursor-trail", "keypad", "key-bars"}


def test_switching_everything_off_is_not_the_same_as_never_asking():
    """The empty list is obeyed; `None` is not a list at all."""
    from bot.handlers.profile.settings_menu import effects

    chosen = Choices()
    effects._store_set(chosen, set())
    assert chosen.effects == ""
    assert effects._on(chosen) == set()


def test_a_switch_is_stored_as_the_engines_own_list():
    from bot.handlers.profile.settings_menu import effects

    chosen = Choices()
    effects._store_set(chosen, {"snake-out", "cursor-trail"})
    # In the engine's order rather than the set's, so the stored text is stable
    # and two people with the same switches have the same row.
    assert chosen.effects == "snake-out,cursor-trail"
    assert effects._on(chosen) == {"snake-out", "cursor-trail"}


def test_the_sub_tabs_survive_a_restart():
    from bot.handlers.dossier import renders

    chosen = Choices(effects="snake-in")
    row = Row()
    renders.remember_settings(row, chosen)
    assert renders.restore_settings(row, Choices()).effects == "snake-in"


def test_the_render_screen_offers_a_way_into_every_sub_tab():
    from bot.handlers.profile.settings_menu import effects

    rows = section._render_kb(Choices(), False, "ru").inline_keyboard
    taps = {b.callback_data for row in rows for b in row}
    for wanted in (f"st:fx:{effects.TAB}", "st:qly", "st:snd", "st:skn"):
        assert wanted in taps, wanted


def test_a_sub_tab_callback_cannot_be_read_as_a_render_setting():
    """`st:rnd:` ends in a catch-all that reads any four-part callback as a
    setting. The sub-tabs use their own prefixes so the two cannot collide."""
    from bot.handlers.profile.settings_menu import effects

    assert not f"st:fx:{effects.TAB}".startswith("st:rnd:")
    for name, _, _ in effects.SWITCHES:
        assert not f"st:fx:{effects.TAB}:{name}".startswith("st:rnd:")
    for prefix in ("st:qly", "st:snd", "st:skn"):
        assert not prefix.startswith("st:rnd:")


# ── the sound sub-tab (`st:snd`) ─────────────────────────────────────────────


def test_a_render_sounds_as_it_always_did_until_somebody_says_otherwise():
    chosen = Choices()
    assert (chosen.music, chosen.hitsounds) == (100, 100)
    assert "со звуком" in chosen.summary()
    assert "%" not in chosen.summary(), "the natural mix is not worth two numbers"


def test_the_summary_names_the_mix_once_it_is_not_the_natural_one():
    assert "музыка 40%" in Choices(music=40).summary()
    # Muted wins: neither level is heard, and saying both would be a lie about
    # what the render will sound like.
    assert "%" not in Choices(mute=True, music=40).summary()


def test_each_half_of_the_mix_is_set_on_its_own():
    from bot.handlers.profile.settings_menu import sound

    chosen = Choices()
    assert sound._apply(chosen, "music", 25)
    assert (chosen.music, chosen.hitsounds) == (25, 100), "the hits followed"
    assert sound._apply(chosen, "hitsounds", 0)
    assert (chosen.music, chosen.hitsounds) == (25, 0)


def test_a_level_the_menu_never_offered_is_refused():
    from bot.handlers.profile.settings_menu import sound

    chosen = Choices()
    assert not sound._apply(chosen, "music", 63)
    assert not sound._apply(chosen, "master", 50)
    assert (chosen.music, chosen.hitsounds) == (100, 100)


def test_the_levels_survive_a_restart():
    from bot.handlers.dossier import renders

    row = Row()
    renders.remember_settings(row, Choices(music=25, hitsounds=75))
    after = renders.restore_settings(row, Choices())
    assert (after.music, after.hitsounds) == (25, 75)


def test_the_render_screen_offers_a_way_into_the_sound_tab():
    rows = section._render_kb(Choices(), False, "ru").inline_keyboard
    taps = {b.callback_data for row in rows for b in row}
    assert "st:snd" in taps


def test_a_sound_callback_cannot_be_read_as_a_render_setting():
    from bot.handlers.profile.settings_menu import sound

    for half in sound.HALVES:
        for level in sound.STEPS:
            assert not f"st:snd:{half}:{level}".startswith("st:rnd:")


def test_the_render_screen_offers_a_way_into_the_quality_tab():
    rows = section._render_kb(Choices(), False, "ru").inline_keyboard
    taps = {b.callback_data for row in rows for b in row}
    assert "st:qly" in taps
    # And no longer draws the settings themselves — that is the whole point of
    # moving them, and a screen showing both would be two ways to set one thing.
    assert not any(str(d).startswith("st:rnd:size:") for d in taps), taps
    assert not any(str(d).startswith("st:rnd:fps:") for d in taps), taps


def test_a_size_is_set_by_the_same_callback_it_always_was():
    """The buttons moved screens; the setting did not move handlers. A keyboard
    somebody had open before the sub-tab existed still works."""
    taps = {
        b.callback_data for b in buttons(section._quality_kb(Choices(), "ru"))
    }
    assert "st:rnd:size:3840x2160" in taps
    assert "st:rnd:fps:120" in taps


def test_the_maps_own_hit_sounds_are_on_by_default():
    """A sound is looked for in the map, then the skin, then the game's own
    defaults — the same order in stable, lazer and danser. Skipping the first
    step is a setting, off by default everywhere."""
    assert Choices().map_hitsounds is True


def test_the_map_switch_lives_in_the_sound_tab():
    from bot.handlers.profile.settings_menu import sound

    # Ticked by default, so the button on offer is the one that turns it off.
    taps = {b.callback_data for b in buttons(sound._kb(Choices(), "ru"))}
    assert "st:rnd:map_hitsounds:0" in taps
    off = {b.callback_data for b in buttons(sound._kb(Choices(map_hitsounds=False), "ru"))}
    assert "st:rnd:map_hitsounds:1" in off
    # And nowhere else — each switch is drawn in exactly one place, which is
    # what lets a tap redraw the screen it was on.
    for kb in (section._render_kb(Choices(), False, "ru"), section._quality_kb(Choices(), "ru")):
        assert not any(
            (b.callback_data or "").startswith("st:rnd:map_hitsounds:")
            for b in buttons(kb)
        )


def test_the_map_switch_survives_a_restart():
    from bot.handlers.dossier import renders

    row = Row()
    renders.remember_settings(row, Choices(map_hitsounds=True))
    assert renders.restore_settings(row, Choices()).map_hitsounds is True


def test_the_artwork_dim_only_appears_once_the_artwork_does():
    """It is not a question until there is a picture to ask it about."""
    off = {b.callback_data for b in buttons(section._quality_kb(Choices(), "ru"))}
    assert not any(str(d).startswith("st:qly:dim:") for d in off)
    on = {
        b.callback_data
        for b in buttons(section._quality_kb(Choices(background=True), "ru"))
    }
    assert "st:qly:dim:50" in on


def test_the_dim_is_the_engines_until_somebody_chooses():
    """Null is "whatever it settles on", not a stored copy of today's figure."""
    assert Choices().dim is None


def test_the_dim_survives_a_restart():
    from bot.handlers.dossier import renders

    row = Row()
    renders.remember_settings(row, Choices(dim=25))
    assert renders.restore_settings(row, Choices()).dim == 25


def test_the_skin_list_left_the_render_screen():
    """It was three buttons when it was written and is however many `.osk`
    files somebody has sent since."""
    taps = {b.callback_data for b in buttons(section._render_kb(Choices(), False, "ru"))}
    assert "st:skn" in taps
    assert not any(str(d).startswith("st:rnd:skin:") for d in taps)
