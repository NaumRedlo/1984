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

class _Noise:

    def __init__(self, seed: int):
        self.state = seed

    def uniform(self, low: float, high: float) -> float:
        self.state = (self.state * 1103515245 + 12345) % 2 ** 31
        return low + (high - low) * self.state / 2 ** 31

def _map(kind: str, noise: _Noise) -> dict:
    jitter = lambda: noise.uniform(0.8, 1.2)
    if kind == "aim":
        return _features(**{"spacing_radii.p90": 6.0 * jitter(), "aim_windows.2s.p90": 5.0 * jitter(), "speed_px_ms.p90": 4.0 * jitter(),
                            "streams.share": 0.05 * jitter(), "effective.ar": 9.3, "effective.cs": 4.0})
    return _features(**{"streams.share": 0.7 * jitter(), "streams.lengths.16-31": 40.0 * jitter(), "streams.lengths.32-plus": 10.0 * jitter(),
                        "tap_windows.10s.p90": 9.0 * jitter(), "tap_windows.2s.p90": 9.0 * jitter(), "interval_ms.median": 0.5 * jitter(),
                        "spacing_radii.p90": 1.5 * jitter(), "spacing_radii.median": 0.5 * jitter(), "effective.ar": 9.3, "effective.cs": 4.0})

def _examples(count: int = 24, years=(2021, 2022, 2023)) -> list[kamerton.Example]:
    noise = _Noise(7)
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
    noise = _Noise(11)
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

def test_tied_scores_count_as_half_a_win():
    assert kamerton._auc([0.0, 0.0, 0.0, 0.0], [True, False, True, False]) == pytest.approx(0.5)
    assert kamerton._auc([0.0, 1.0, 2.0, 3.0], [False, False, True, True]) == pytest.approx(1.0)


def test_a_stacked_model_survives_saving_and_explains_with_its_skills():
    import json

    from services.duels.slot_model import SlotModel

    model = kamerton.StackedModel.fit(_examples(30), steps=60)
    body = json.loads(json.dumps(model.dump()))
    loaded = kamerton.load_model(body)
    assert isinstance(loaded, kamerton.StackedModel)
    sample = _examples(3)[0]
    before, after = kamerton.decide(model, sample), kamerton.decide(loaded, sample)
    assert before.probabilities == pytest.approx(after.probabilities) and before.slot == after.slot
    assert after.reasons and all(name in loaded.slots.paths for name, _ in after.reasons)
    assert set(kamerton.SKILL_PATHS) <= set(loaded.slots.paths) and "NFDT:skill.aim" in kamerton.SKILL_PATHS
    with pytest.raises(ValueError):
        kamerton.load_model({**body, "features": "features-v1"})
    plain = SlotModel.fit([[1.0, 2.0], [2.0, 1.0]], ["NM1", "NM2"], paths=("a", "b"))
    with pytest.raises((KeyError, TypeError, ValueError)):
        kamerton.load_model({**plain.dump(), "paths": ["a", "b"]})


def test_a_secondary_slot_is_a_second_right_answer_for_the_slot_layer_and_changes_the_fingerprint():
    from dataclasses import replace

    plain = _examples(30)
    both = [replace(example, secondary="HR1") if example.slot == "NM1" else example for example in plain]
    alone, taught = kamerton.StackedModel.fit(plain, steps=120), kamerton.StackedModel.fit(both, steps=120)
    assert "HR1" not in alone.slots.classes and "HR1" in taught.slots.classes
    aim = _map("aim", _Noise(5))
    odds = kamerton.decide(taught, kamerton.Example(aim, {"NFDT": aim, "NFHR": aim}, "", 0, 0)).probabilities
    assert odds["HR1"] > 0.1, "the shared aim skill under HR is now a target of its own"
    assert kamerton.fingerprint(plain) != kamerton.fingerprint(both), "a new secondary slot is new data to learn from"


def test_a_refused_map_is_not_counted_as_admitted_and_reserved_classes_are_scored():
    from services.duels.slot_model import Prediction

    class Fixed:
        def __init__(self, *answers):
            self.answers = list(answers)

        def predict(self, features, base_ar, hr_cs, views=None):
            return self.answers.pop(0)

    held = [kamerton.Example({}, {}, slot, at, 2024, secondary="HR1" if slot == "NM1" else None) for at, slot in enumerate(("NM1", "NM1", "NM2", "FM4"))]
    model = Fixed(Prediction(None, 0.95, 0.9, {"NM1": 0.95, "NM2": 0.05}, True),
                  Prediction("NM1", 0.9, 0.8, {"NM1": 0.9, "NM2": 0.1}, False),
                  Prediction("NM2", 0.55, 0.1, {"NM2": 0.55, "NM1": 0.45}, False),
                  Prediction(None, 0.7, 0.4, {"FM4": 0.7, "NM2": 0.3}, False))
    judged = kamerton.judged_on(model, held)
    assert [one.refusal for one in judged] == ["outside", None, "below_threshold", "not_working"]
    found = kamerton.summary(judged)
    assert found["confident"] == {"count": 1, "precision": 1.0, "share": 0.25}, "the map outside the trained range is not a confident answer"
    assert found["admitted"]["checked"] == 4 and found["admitted"]["count"] == 1 and found["admitted"]["refused"] == {
        "count": 3, "share": 0.75, "outside": 1, "not_working": 1, "below_threshold": 1}
    assert found["per_slot"]["NM1"]["recall"] == 1.0, "the catalogue figures still see what the model thought"
    assert found["per_slot"]["FM4"]["f1"] == 1.0 and found["macro_f1_all"] > found["macro_f1"] - 1e-9


def test_reasons_carry_the_level_of_the_very_row_the_prediction_used():
    from services.duels import desk

    model = kamerton.StackedModel.fit(_examples(30), steps=60)
    aim = _map("aim", _Noise(3))
    prediction = kamerton.decide(model, kamerton.Example(aim, {"NFDT": aim, "NFHR": aim}, "", 0, 0))
    assert set(prediction.levels) == {name for name, _ in prediction.reasons}
    weights = model.slots.weights[model.slots.classes.index(max(prediction.probabilities, key=prediction.probabilities.get))]
    assert all(weight == pytest.approx(weights[model.slots.paths.index(name)] * prediction.levels[name]) for name, weight in prediction.reasons)
    stream = _map("stream", _Noise(3))
    explained = desk._explain(model, stream, 9.3, 5.2, {"NFDT": stream, "NFHR": stream})
    assert any(abs(reason["level"]) > 0.5 for reason in explained["reasons"]), "a stream map stands out, and the desk is told so"


def test_a_model_that_cannot_answer_is_refused_before_it_is_used():
    import json
    import math

    body = json.loads(json.dumps(kamerton.StackedModel.fit(_examples(24), steps=30).dump()))
    assert isinstance(kamerton.checked(body), kamerton.StackedModel)
    broken = [
        {**body, "skills": {**body["skills"], "weights": []}},
        {**body, "skills": {**body["skills"], "weights": [row[:-1] for row in body["skills"]["weights"]]}},
        {**body, "slots": {**body["slots"], "weights": [row[:3] for row in body["slots"]["weights"]]}},
        {**body, "slots": {**body["slots"], "bias": [math.nan] * len(body["slots"]["bias"])}},
        {**body, "slots": {**body["slots"], "spread": [0.0] * len(body["slots"]["spread"])}},
        {**body, "skills": {**body["skills"], "centre": body["skills"]["centre"][:-1]}},
    ]
    for wrong in broken:
        with pytest.raises(kamerton.BROKEN):
            kamerton.checked(wrong)
