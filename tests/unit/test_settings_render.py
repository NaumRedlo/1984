"""The render section of `sts`, and the one thing on it that is not a taste.

Size, frame rate and sound are preferences. "Send replay data to the developer"
is permission to take somebody's files, and the tests that matter here are the
ones about it being off unless it was asked for.
"""

import pytest

from bot.handlers.dossier.renders import Choices
from bot.handlers.profile.settings_menu import render as section
from utils.i18n import t


def buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


# ── the preferences ───────────────────────────────────────────────────────

def test_every_option_offered_is_one_the_menu_will_accept():
    """The keyboard and the handler read the same table, so a button can never
    lead to "no such setting" — which is what a second list would eventually
    produce."""
    chosen = Choices()
    for key, values in section.OPTIONS.items():
        for value, _ in values:
            assert section._apply(chosen, key, value), f"{key}={value}"


def test_a_value_the_menu_never_offered_is_refused():
    """Callback data is user input, and an old keyboard outlives the option it
    was drawn for."""
    chosen = Choices()
    assert not section._apply(chosen, "size", "4000x3000")
    assert not section._apply(chosen, "nonsense", "1")
    assert chosen.size == Choices().size, "and nothing was changed on the way"


def test_what_is_already_true_is_marked_rather_than_hidden():
    chosen = Choices(size="854x480", fps=30, mute=True)
    marked = [b.text for b in buttons(section._render_kb(chosen, False, "en"))
              if b.text.startswith("● ") and ":skin:" not in (b.callback_data or "")]
    assert marked == ["● 480p", "● 30", f"● {t('sts.rnd.sound_off', 'en')}"]


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
    keys = ["sts.kb.render", "sts.rnd.body", "sts.rnd.size", "sts.rnd.fps",
            "sts.rnd.mute", "sts.rnd.share", "sts.rnd.share_off",
            "sts.rnd.share_needs_account", "sts.rnd.unknown"]
    missing = [k for k in keys if t(k, lang) == k]
    assert not missing, missing


# ── choosing a skin ───────────────────────────────────────────────────────

def test_the_engines_own_look_is_always_offered_and_is_the_default(monkeypatch):
    """Whatever is in the store, there is always something to fall back to —
    and it is what a fresh account renders in."""
    monkeypatch.setattr(section.skins, "available", lambda: [])
    rows = section._skin_rows(Choices(), "en")
    assert len(rows) == 1
    assert rows[0][0].text.startswith("● "), "and it is the one marked"
    assert rows[0][0].callback_data.endswith(f":{section.DEFAULT_SKIN}")


def test_every_stored_skin_gets_a_button(monkeypatch):
    monkeypatch.setattr(section.skins, "available", lambda: ["doki", "rafis"])
    names = [row[0].callback_data.split(":", 3)[3]
             for row in section._skin_rows(Choices(), "en")]
    assert names == [section.DEFAULT_SKIN, "doki", "rafis"]


def test_the_chosen_skin_is_the_marked_one(monkeypatch):
    monkeypatch.setattr(section.skins, "available", lambda: ["doki", "rafis"])
    marked = [row[0].text for row in section._skin_rows(Choices(skin="rafis"), "en")
              if row[0].text.startswith("● ")]
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
