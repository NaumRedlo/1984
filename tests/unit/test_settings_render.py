import pytest

from bot.handlers.dossier.renders import Choices
from bot.handlers.profile.settings_menu import render as section
from bot.handlers.profile.settings_menu import skins as skin_tab
from utils.i18n import t

def buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]

def test_every_switch_drawn_is_one_the_handler_accepts():
    from bot.handlers.profile.settings_menu import sound

    drawn = set()
    for markup in (
        section._render_kb(Choices(), False, "en"),
        section._quality_kb(Choices(background=True)),
        sound._kb(Choices(), "en"),
    ):
        for row in markup.inline_keyboard:
            for button in row:
                parts = (button.callback_data or "").split(":")
                if len(parts) == 4 and parts[0] == "st" and parts[1] == "rnd":
                    drawn.add((parts[2], parts[3]))

    assert drawn, "no switches found — the scan has stopped working"

    import pathlib

    module = (
        pathlib.Path(__file__).resolve().parents[2]
        / "bot/handlers/profile/settings_menu/render.py"
    ).read_text()

    for key, value in sorted(drawn):
        if f'F.data.startswith("st:rnd:{key}:")' in module:
            continue
        assert section._apply(Choices(), key, value), f"drawn but refused: {key}={value}"

def test_every_option_offered_is_one_the_menu_will_accept():
    chosen = Choices()
    for key, rows in section.OPTIONS.items():
        for value, _ in [pair for row in rows for pair in row]:
            assert section._apply(chosen, key, value), f"{key}={value}"
    for key in section.TOGGLES:
        for value in ("0", "1"):
            assert section._apply(chosen, key, value), f"{key}={value}"

def test_a_value_the_menu_never_offered_is_refused():
    chosen = Choices()
    assert not section._apply(chosen, "size", "4000x3000")
    assert not section._apply(chosen, "nonsense", "1")
    assert chosen.size == Choices().size, "and nothing was changed on the way"

def test_a_switch_shows_what_it_is_rather_than_offering_both_halves():
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
    assert len(section.OPTIONS["size"]) > 1
    assert all(len(row) <= 3 for row in section.OPTIONS["size"])

def test_sharing_is_off_until_it_is_switched_on():
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
    for lang in ("en", "ru"):
        label = t("sts.rnd.share", lang)
        assert label and label != "sts.rnd.share", lang
    assert "replay" in t("sts.rnd.share", "en").lower()
    assert "реплея" in t("sts.rnd.share", "ru").lower()

def test_turning_it_on_spells_out_what_is_sent():
    for lang in ("en", "ru"):
        said = t("sts.rnd.share_on", lang).lower()
        assert ".osr" in said, lang
        assert "off" in said or "выключить" in said, "and how to stop"

def test_the_screen_keeps_saying_so_while_it_is_on():
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

def _skin_buttons(rows):
    return [
        b
        for row in rows
        for b in row
        if (b.callback_data or "").startswith("st:rnd:skin:")
    ]

def test_the_engines_own_look_is_always_offered_and_is_the_default(monkeypatch):
    monkeypatch.setattr(skin_tab.store, "available", lambda: [])
    monkeypatch.setattr(skin_tab.store, "stale", lambda: [])
    offered = _skin_buttons(skin_tab.rows(Choices(), "en"))
    assert len(offered) == 1
    assert offered[0].text.startswith("● "), "and it is the one marked"
    assert offered[0].callback_data.endswith(f":{skin_tab.DEFAULT_SKIN}")

def test_every_stored_skin_gets_a_button(monkeypatch):
    monkeypatch.setattr(skin_tab.store, "available", lambda: ["doki", "rafis"])
    monkeypatch.setattr(skin_tab.store, "stale", lambda: [])
    names = [
        b.callback_data.split(":", 3)[3]
        for b in _skin_buttons(skin_tab.rows(Choices(), "en"))
    ]
    assert names == [skin_tab.DEFAULT_SKIN, "doki", "rafis"]

def test_skins_go_three_to_a_row(monkeypatch):
    monkeypatch.setattr(skin_tab.store, "available", lambda: [f"s{n}" for n in range(7)])
    monkeypatch.setattr(skin_tab.store, "stale", lambda: [])
    rows = [
        row
        for row in skin_tab.rows(Choices(), "en")
        if all((b.callback_data or "").startswith("st:rnd:skin:") for b in row)
    ]
    assert [len(row) for row in rows] == [3, 3, 2]

def test_the_chosen_skin_is_the_marked_one(monkeypatch):
    monkeypatch.setattr(skin_tab.store, "available", lambda: ["doki", "rafis"])
    marked = [b.text for row in skin_tab.rows(Choices(skin="rafis"), "en")
              for b in row if b.text.startswith("● ")]
    assert marked == ["● rafis"]

def test_a_skin_arrives_by_being_sent_rather_than_typed():
    for lang in ("en", "ru"):
        assert ".osk" in t("sts.rnd.skin", lang), lang

def test_nothing_shown_as_a_popup_is_longer_than_telegram_allows():
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
    body = section._text(Choices(), sharing=True, lang="ru")
    assert ".osr" in body and len(t("sts.rnd.share_on", "ru")) > 200

class Row:

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
    from bot.handlers.dossier import renders

    chosen = Choices(size="1920x1080", fps=30, mute=True, skin="doki")
    row = Row()
    renders.remember_settings(row, chosen)

    after = renders.restore_settings(row, Choices())
    assert (after.size, after.fps, after.mute, after.skin) == (
        "1920x1080", 30, True, "doki",
    )

def test_an_account_that_never_chose_anything_keeps_the_defaults():
    from bot.handlers.dossier import renders

    fresh = Choices()
    after = renders.restore_settings(Row(), Choices())
    assert (after.size, after.fps, after.mute) == (fresh.size, fresh.fps, fresh.mute)

def test_a_render_without_an_account_still_has_settings():
    from bot.handlers.dossier import renders

    chosen = Choices(size="854x480")
    assert renders.restore_settings(None, chosen) is chosen

def test_only_what_is_past_the_old_ceiling_is_rationed():
    assert not Choices(size="1920x1080", fps=60).heavy()
    assert not Choices(size="854x480", fps=30).heavy()
    assert Choices(size="1920x1080", fps=120).heavy()
    assert Choices(size="2560x1440", fps=60).heavy()
    assert Choices(size="3840x2160", fps=120).heavy()

def test_a_size_that_cannot_be_read_is_not_treated_as_expensive():
    assert not Choices(size="nonsense").heavy()

def test_the_ration_is_counted_per_day_and_rolls_over():
    from bot.handlers.dossier import renders

    row = Row()
    assert renders.heavy_left(row) == renders.HEAVY_PER_DAY
    for _ in range(renders.HEAVY_PER_DAY):
        renders.spend_heavy(row)
    assert renders.heavy_left(row) == 0

    row.heavy_renders_on = "2000-01-01"
    assert renders.heavy_left(row) == renders.HEAVY_PER_DAY

def test_an_account_that_does_not_exist_has_no_ration():
    from bot.handlers.dossier import renders

    assert renders.heavy_left(None) == 0

def test_the_screen_says_what_is_left_before_anybody_asks():
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
    assert "фон" not in Choices().summary()
    assert "фон" in Choices(background=True).summary()
    assert "без интерфейса" in Choices(bare=True).summary()

def test_every_switch_belongs_to_a_group_that_exists():
    from bot.handlers.profile.settings_menu import effects

    for name, group, _ in effects.SWITCHES:
        assert group in effects.GROUPS, f"{name} is filed under a group nobody shows"

    for group in effects.GROUPS:
        assert any(belongs == group for _, belongs, _ in effects.SWITCHES), group

def test_the_bot_and_the_engine_agree_on_which_movements_exist():
    import os
    import pathlib
    import re

    import pytest

    from bot.handlers.profile.settings_menu import effects
    from dossier.settings import DOSSIER_BIN

    beside = pathlib.Path(DOSSIER_BIN).resolve().parents[2]
    skin = beside / "crates/dossier-render/src/skin.rs"
    if not skin.is_file():
        pytest.skip(
            f"no engine source at {skin} — this bot is running a build it did "
            f"not compile, so there is nothing here to disagree with"
        )
    source = skin.read_text()
    listed = re.search(r"ALL: \[&'static str; \d+\] = \[(.*?)\];", source, re.S)
    assert listed, "the engine's list of movements moved — find it and fix this"
    names = re.findall(r'"([a-z-]+)"', listed.group(1))
    assert names == [name for name, _, _ in effects.SWITCHES]

    for name, _, default in effects.SWITCHES:
        field = name.replace("-", "_")
        found = re.search(rf"^\s+{field}: (true|false),", source, re.M)
        assert found, f"the engine has no default for {name}"
        assert (found.group(1) == "true") is default, name

def test_a_person_who_never_opened_the_sub_tabs_gets_the_engines_defaults():
    from bot.handlers.profile.settings_menu import effects

    fresh = Choices()
    assert fresh.effects is None, "nothing is stored until something is chosen"
    assert effects._on(fresh) == {"cursor-trail", "keypad", "key-bars", "unstable-rate"}

def test_switching_everything_off_is_not_the_same_as_never_asking():
    from bot.handlers.profile.settings_menu import effects

    chosen = Choices()
    effects._store_set(chosen, set())
    assert chosen.effects == ""
    assert effects._on(chosen) == set()

def test_a_switch_is_stored_as_the_engines_own_list():
    from bot.handlers.profile.settings_menu import effects

    chosen = Choices()
    effects._store_set(chosen, {"snake-out", "cursor-trail"})

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
    from bot.handlers.profile.settings_menu import effects

    assert not f"st:fx:{effects.TAB}".startswith("st:rnd:")
    for name, _, _ in effects.SWITCHES:
        assert not f"st:fx:{effects.TAB}:{name}".startswith("st:rnd:")
    for prefix in ("st:qly", "st:snd", "st:skn"):
        assert not prefix.startswith("st:rnd:")

def test_a_render_sounds_as_it_always_did_until_somebody_says_otherwise():
    chosen = Choices()
    assert (chosen.music, chosen.hitsounds) == (100, 100)
    assert "со звуком" in chosen.summary()
    assert "%" not in chosen.summary(), "the natural mix is not worth two numbers"

def test_the_summary_names_the_mix_once_it_is_not_the_natural_one():
    assert "музыка 40%" in Choices(music=40).summary()

    assert "%" not in Choices(mute=True, music=40).summary()

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

def test_the_render_screen_offers_a_way_into_the_quality_tab():
    rows = section._render_kb(Choices(), False, "ru").inline_keyboard
    taps = {b.callback_data for row in rows for b in row}
    assert "st:qly" in taps

    assert not any(str(d).startswith("st:rnd:size:") for d in taps), taps
    assert not any(str(d).startswith("st:rnd:fps:") for d in taps), taps

def test_the_maps_own_hit_sounds_are_on_by_default():
    assert Choices().map_hitsounds is True

def test_the_map_switch_lives_in_the_sound_tab():
    from bot.handlers.profile.settings_menu import sound

    taps = {b.callback_data for b in buttons(sound._kb(Choices(), "ru"))}
    assert "st:rnd:map_hitsounds:0" in taps
    off = {b.callback_data for b in buttons(sound._kb(Choices(map_hitsounds=False), "ru"))}
    assert "st:rnd:map_hitsounds:1" in off

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

def test_the_dim_is_the_engines_until_somebody_chooses():
    assert Choices().dim is None

def test_the_dim_survives_a_restart():
    from bot.handlers.dossier import renders

    row = Row()
    renders.remember_settings(row, Choices(dim=25))
    assert renders.restore_settings(row, Choices()).dim == 25

def test_the_skin_list_left_the_render_screen():
    taps = {b.callback_data for b in buttons(section._render_kb(Choices(), False, "ru"))}
    assert "st:skn" in taps
    assert not any(str(d).startswith("st:rnd:skin:") for d in taps)

def test_the_engine_is_told_a_multiplier_not_a_percentage():
    from dossier.runner import _render_args

    args = _render_args(
        "video", "r.osr", "/songs", "o.mp4",
        size="1280x720", fps=60, mute=True, skin=None,
        leaderboard=None, my_pictures=(None, None), meter=150,
    )
    assert "--meter-scale" in args
    assert args[args.index("--meter-scale") + 1] == "1.50"

def test_a_meter_nobody_chose_is_left_to_the_engine():
    from dossier.runner import _render_args

    args = _render_args(
        "video", "r.osr", "/songs", "o.mp4",
        size="1280x720", fps=60, mute=True, skin=None,
        leaderboard=None, my_pictures=(None, None),
    )
    assert "--meter-scale" not in args

def test_the_engine_is_told_the_volume_only_when_one_was_chosen():
    from dossier.runner import _render_args

    common = dict(
        size="1280x720", fps=60, mute=True, skin=None,
        leaderboard=None, my_pictures=(None, None),
    )
    silent = _render_args("video", "r.osr", "/songs", "o.mp4", **common)
    assert "--volume" not in silent
    loud = _render_args("video", "r.osr", "/songs", "o.mp4", volume=150, **common)
    assert loud[loud.index("--volume") + 1] == "150"

def test_the_slider_ball_tint_is_a_movement_like_the_rest():
    from bot.handlers.profile.settings_menu import effects

    names = [name for name, _, _ in effects.SWITCHES]
    assert "slider-ball-tint" in names
    assert dict((n, g) for n, g, _ in effects.SWITCHES)["slider-ball-tint"] == "slider"

def test_the_summary_line_is_written_in_the_readers_language():
    english = Choices(mute=True, background=True, bare=True).summary("en")
    assert "муз" not in english and "звук" not in english and "фон" not in english
    assert "muted" in english and "map background" in english

    russian = Choices(mute=True, background=True).summary("ru")
    assert "без звука" in russian and "фон карты" in russian

def test_the_mix_is_named_only_when_it_is_not_the_natural_one():
    assert "%" not in Choices().summary("en")
    assert "50%" in Choices(music=50).summary("en")

def test_a_render_sub_screen_has_one_row_of_navigation_and_not_two():
    from bot.handlers.profile.settings_menu import effects, sound

    for markup in (
        section._quality_kb(Choices()),
        sound._kb(Choices(), "en"),
        effects._kb(Choices(), "en"),
    ):
        exits = [
            b
            for row in markup.inline_keyboard
            for b in row
            if b.callback_data in ("st:rnd", "st:home", "st:close")
        ]
        assert len(exits) == 2, [b.text for b in exits]
        assert markup.inline_keyboard[-1] == exits, "and they are the last row"

def test_the_movements_are_paired_rather_than_stacked():
    from bot.handlers.profile.settings_menu import effects

    rows = [
        row
        for row in effects._kb(Choices(), "en").inline_keyboard
        if all(b.callback_data.startswith("st:fx:") for b in row)
    ]
    assert rows, "no switch rows found"
    assert any(len(row) == 2 for row in rows), "nothing was paired"
    assert all(len(row) <= 2 for row in rows), "a row wider than a pair"

def test_the_picker_puts_your_own_skins_above_everybody_elses(monkeypatch):
    from bot.handlers.profile.settings_menu import skins as skin_tab

    monkeypatch.setattr(skin_tab.store, "available", lambda: ["mine1", "theirs", "mine2"])
    monkeypatch.setattr(skin_tab.store, "stale", lambda: [])
    monkeypatch.setattr(
        skin_tab.store, "owner_of", lambda name: 7 if name.startswith("mine") else 99
    )
    monkeypatch.setattr(
        skin_tab.store,
        "by_owner",
        lambda tg: (["mine1", "mine2"], ["theirs"]) if tg == 7 else ([], ["mine1", "theirs", "mine2"]),
    )

    names = [
        b.callback_data.split(":", 3)[3]
        for row in skin_tab.rows(Choices(), "en", 7)
        for b in row
        if b.callback_data.startswith("st:rnd:skin:")
    ]
    assert names == ["mine1", "mine2", skin_tab.DEFAULT_SKIN, "theirs"]

def test_somebody_who_has_sent_none_still_gets_both_headings(monkeypatch):
    from bot.handlers.profile.settings_menu import skins as skin_tab

    monkeypatch.setattr(skin_tab.store, "available", lambda: ["theirs"])
    monkeypatch.setattr(skin_tab.store, "stale", lambda: [])
    monkeypatch.setattr(skin_tab.store, "by_owner", lambda tg: ([], ["theirs"]))

    texts = [b.text for row in skin_tab.rows(Choices(), "en", 7) for b in row]
    assert any("Yours" in text for text in texts)
    assert any("Shared" in text for text in texts)
    assert any("send an .osk" in text for text in texts)

def test_a_skin_nobody_claimed_is_shared_rather_than_somebodys(tmp_path, monkeypatch):
    from dossier import skins as store

    monkeypatch.setattr(store, "store_dir", lambda: str(tmp_path))
    (tmp_path / "old").mkdir()
    (tmp_path / "owned").mkdir()
    (tmp_path / "owned" / store.STAMP).write_text('{"extract_version": 1, "owner": 7}')

    assert store.owner_of("old") is None
    assert store.owner_of("owned") == 7
    assert store.by_owner(7) == (["owned"], ["old"])
    assert store.by_owner(None) == ([], ["old", "owned"])
