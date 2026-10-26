import random

import pytest

from services.duels import kamerton
from services.duels.slot_model import PATHS

def _features(**values) -> dict:
    found: dict = {"objects": 1000}
    for path in PATHS:
        node = found
        *parents, leaf = path.split(".")
        for key in parents:
            node = node.setdefault(key, {})
        node[leaf] = values.get(path, 1.0)
    return found

def _map(kind: str, noise: random.Random) -> dict:
    jitter = lambda: noise.uniform(0.8, 1.2)
    if kind == "aim":
        return _features(**{"spacing_radii.p90": 6.0 * jitter(), "aim_windows.2s.p90": 5.0 * jitter(), "speed_px_ms.p90": 4.0 * jitter(),
                            "streams.share": 0.05 * jitter(), "effective.ar": 9.3, "effective.cs": 4.0})
    return _features(**{"streams.share": 0.7 * jitter(), "streams.lengths.16-31": 40.0 * jitter(), "streams.lengths.32-plus": 10.0 * jitter(),
                        "spacing_radii.p90": 1.5 * jitter(), "effective.ar": 9.3, "effective.cs": 4.0})

def _examples(count: int = 24, years=(2021, 2022, 2023)) -> list[kamerton.Example]:
    noise = random.Random(7)
    found = []
    for at in range(count):
        year = years[at % len(years)]
        slot, kind = [("NM1", "aim"), ("NM2", "stream"), ("DT1", "aim")][at % 3]
        plain = _map(kind, noise)
        found.append(kamerton.Example(plain, {"NFDT": plain, "NFHR": plain}, slot, 1000 + at, year))
    return found

def test_a_slot_is_a_mod_and_skills_and_a_second_slot_softens_the_target():
    assert kamerton.PARTS["HR1"] == ("HR", {"aim": 1.0}) and kamerton.PARTS["NM1"][1] == kamerton.PARTS["HR1"][1]
    assert kamerton.skill_target("HD3") == {"tech": 0.5, "alt": 0.5}
    target = kamerton.skill_target("NM1", "NM4")
    assert target == {"aim": pytest.approx(0.75), "tech": pytest.approx(0.25)}
    assert kamerton.skill_target("NM1", "HR1") == {"aim": pytest.approx(1.0)}, "the same skill in another mod is no conflict"
    assert set(kamerton.SIGNATURES) == set(kamerton.SKILLS) and all(path in PATHS for parts in kamerton.SIGNATURES.values() for path, _ in parts)

def test_the_two_layers_share_a_skill_across_mods_and_tell_aim_from_streams():
    examples = _examples()
    model = kamerton.SkillModModel.fit(examples, steps=150)
    noise = random.Random(11)
    aim, stream = _map("aim", noise), _map("stream", noise)
    aimed = model.scores(aim, {"NFDT": aim, "NFHR": aim}, 9.3, 5.2)
    streamed = model.scores(stream, {"NFDT": stream, "NFHR": stream}, 9.3, 5.2)
    assert max(aimed, key=aimed.get) in ("NM1", "DT1") and aimed["NM1"] + aimed["DT1"] > 0.5
    assert max(streamed, key=streamed.get) == "NM2"
    assert "HR2" not in aimed, "HR2 keeps its circle size rule"
    assert sum(aimed.values()) == pytest.approx(1.0)
    prediction = kamerton.decide(model, kamerton.Example(aim, {"NFDT": aim, "NFHR": aim}, "", 0, 0))
    assert prediction.slot in ("NM1", "DT1") and 0 <= prediction.margin <= prediction.probability <= 1

def test_signatures_are_measured_against_labels_and_both_models_are_compared_on_held_out_years():
    examples = _examples(30)
    truth = kamerton.signature_truth(examples)
    assert truth["aim"] > 0.9 and truth["stream"] > 0.9
    assert truth["precision"] is None, "a skill without labelled maps cannot be measured"
    found = kamerton.compare(examples, steps=60, report_to=lambda said: None)
    for scope in ("working", "catalogue"):
        assert set(found[scope]) == {"current", "skill_mod", "stacked"}
        assert {"macro_f1", "per_slot", "confident", "top2"} <= set(found[scope]["skill_mod"])
    assert found["working"]["skill_mod"]["count"] == len(examples)
