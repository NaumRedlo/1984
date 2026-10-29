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
])
def test_cut_sped_up_and_tv_size_versions_are_spotted(title, version, trimmed):
    assert tags.trimmed(title, version) is trimmed
