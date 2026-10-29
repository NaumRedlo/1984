import pytest

from services.duels import tags


@pytest.mark.parametrize("said,tournaments,stages,slots", [
    ("owc2024 qf nm2 quarterfinals osu! world cup melodic", ["owc2024"], ["qf"], ["NM2"]),
    ("owc2020 owc2020qffm1 osu! world cup freemod", ["owc2020"], ["qf"], ["FM1"]),
    ("sentinel tournament ver trt2 2 two", ["roundtable2"], [], []),
    ("The Roundtable custom map", ["roundtable"], [], []),
    ("moscow osu! event 2025 moe2025 main stage gf grand finals", ["moe2025"], ["gf"], []),
    ("gf grand finals tb tiebreaker custom perennial", ["perennial"], ["gf"], ["TB"]),
    ("Round of 16 OWC 2023 HD1", ["owc2023"], ["ro16"], ["HD1"]),
    ("osu! World Cup 2022 Semi Finals DT4", ["owc2022"], ["sf"], ["DT4"]),
    ("owc2025 qlf qualifiers nm3", ["owc2025"], ["qualifiers"], ["NM3"]),
    ("sfx final fantasy nm1 f", [], [], []),
    (None, [], [], []),
])
def test_tournament_tags_are_read_in_every_spelling(said, tournaments, stages, slots):
    found = tags.read(said)
    assert (found["tournaments"], found["stages"], found["slots"]) == (tournaments, stages, slots)
    assert found["raw"] == (said or "")


@pytest.mark.parametrize("title,version,trimmed", [
    ("Kyouran Hey Kids!! (TV Size)", "Insane", True), ("Song (Cut Ver.)", "Extra", True), ("Song (Sped Up Ver.)", "Expert", True),
    ("Song", "Speed Up", True), ("Song", "TV Ver.", True), ("Speedcore 300", "Extra", False), ("Blue Zenith", "FOUR DIMENSIONS", False),
    ("Song (Game Ver.)", "Insane", False), ("Song", "Game Ver. Extra", False),
])
def test_cut_sped_up_and_tv_size_versions_are_spotted(title, version, trimmed):
    assert tags.trimmed(title, version) is trimmed



def test_a_description_names_the_tournament_in_words_but_plain_words_are_no_stage():
    said = ("<div>This map was used in the <a href='x'>osu! World Cup 2024</a> Round of 16 as Hard Rock 2 and in the final chorus "
            "the streams get faster.</div>")
    found = tags.read("", said)
    assert (found["tournaments"], found["stages"], found["slots"]) == (["owc2024"], ["ro16"], ["HR2"])
    assert tags.read("", "Mapped for the Moscow osu! Event 2025, Free Mod 3, Grand Finals &amp; tie breaker")["slots"] == ["FM3", "TB"]
    assert tags.read("", "a hidden 1 gem with a final section")["tournaments"] == []
    assert tags.read("perennial", "used as NoMod 4 in the finals")["slots"] == ["NM4"]
    assert tags.read("perennial", "used as NoMod 4 in the finals")["stages"] == []


async def test_a_set_description_is_asked_once_and_kept():
    class Osu:
        asked = 0

        async def get_beatmapset(self, set_id):
            Osu.asked += 1
            return {"description": {"description": "<p>osu! World Cup 2023 Semifinals</p>"}}

    tags.described_sets.clear()
    osu = Osu()
    assert await tags.description_of(osu, 77) == await tags.description_of(osu, 77)
    assert Osu.asked == 1 and tags.read("", await tags.description_of(osu, 77))["stages"] == ["sf"]
    assert await tags.description_of(None, 77) == "" and await tags.description_of(osu, None) == ""
