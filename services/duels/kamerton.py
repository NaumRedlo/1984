import math
from dataclasses import dataclass
from typing import Any, Callable, Optional

from services.duels import catalog
from services.duels.rules import SLOTS
from services.duels.slot_model import (HD2_MOST_AR, HR2_LEAST_CS, OTHER, PATHS, SEEN, Normaliser, Prediction, SlotModel, _get, _softmax,
                                       report, seen_vector, vector)

SKILLS = ("aim", "stream", "alt", "speed", "burst", "tech", "reading", "gimmick", "precision", "mix")
MODS = ("NM", "HD", "HR", "DT", "FM")
VIEW_OF = {"NM": "NF", "HD": "NF", "HR": "NFHR", "DT": "NFDT", "FM": "NF"}
PARTS: dict[str, tuple[str, dict[str, float]]] = {
    "NM1": ("NM", {"aim": 1.0}), "NM2": ("NM", {"stream": 1.0}), "NM3": ("NM", {"alt": 1.0}), "NM4": ("NM", {"tech": 1.0}),
    "NM5": ("NM", {"speed": 1.0}), "NM6": ("NM", {"gimmick": 0.5, "reading": 0.5}),
    "HD1": ("HD", {"aim": 1.0}), "HD2": ("HD", {"reading": 1.0}), "HD3": ("HD", {"tech": 0.5, "alt": 0.5}),
    "HR1": ("HR", {"aim": 1.0}), "HR2": ("HR", {"precision": 1.0}), "HR3": ("HR", {"tech": 1.0}),
    "DT1": ("DT", {"aim": 1.0}), "DT2": ("DT", {"speed": 1.0}), "DT3": ("DT", {"burst": 1.0}), "DT4": ("DT", {"tech": 0.5, "gimmick": 0.5}),
    "FM1": ("FM", {"aim": 1.0}), "FM2": ("FM", {"reading": 0.5, "gimmick": 0.5}), "FM3": ("FM", {"tech": 1.0}), "FM4": ("FM", {"speed": 1.0}),
    "TB": ("NM", {"mix": 1.0}),
}
SIGNATURES: dict[str, tuple[tuple[str, float], ...]] = {
    "aim": (("spacing_radii.median", 1.0), ("speed_px_ms.median", 1.0), ("spacing_radii.p90", 1.0), ("angle_deg.median", -1.0)),
    "stream": (("tap_windows.10s.p90", 1.0), ("tap_windows.2s.p90", 1.0), ("interval_ms.median", -1.0), ("angle_deg.median", 1.0)),
    "alt": (("sliders.speed.median", 1.0), ("sliders.speed.p90", 1.0), ("aim_windows.2s.median", 1.0), ("speed_px_ms.p90", 1.0)),
    "speed": (("speed_px_ms.median", -1.0), ("spacing_radii.p90", -1.0), ("reading.visible.p90", 1.0)),
    "burst": (("aim_windows.2s.p90", -1.0), ("aim_windows.10s.p90", -1.0), ("speed_px_ms.p99", -1.0)),
    "tech": (("sliders.share", 1.0), ("sliders.speed.p90", 1.0), ("streams.chains", -1.0), ("speed_px_ms.p90", 1.0)),
    "reading": (("effective.ar", -1.0), ("reading.visible.median", 1.0), ("streams.chains", 1.0), ("sliders.share", -1.0)),
    "gimmick": (("effective.ar", -1.0), ("effective.od", -1.0), ("aim_windows.2s.median", -1.0)),
    "precision": (("effective.cs", 1.0), ("length_seconds", -1.0), ("aim_windows.2s.max", -1.0)),
    "mix": (("length_seconds", 1.0), ("aim_windows.2s.max", 1.0), ("tap_windows.2s.max", 1.0)),
}
UNKNOWN_MOD_WEIGHT = 0.3
SECONDARY_SHARE = 0.25
VERSION = "kamerton-skills-v1"

def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, value))))

def profile(z_row: list[float]) -> list[float]:
    found = []
    for skill in SKILLS:
        parts = SIGNATURES[skill]
        found.append(_sigmoid(1.5 * sum(sign * z_row[PATHS.index(path)] for path, sign in parts) / len(parts)))
    return found

def skill_target(slot: str, secondary: Optional[str] = None) -> dict[str, float]:
    target: dict[str, float] = {}
    shares = [(slot, 1.0 - SECONDARY_SHARE), (secondary, SECONDARY_SHARE)] if secondary in PARTS and secondary != slot else [(slot, 1.0)]
    for name, share in shares:
        for skill, part in PARTS[name][1].items():
            target[skill] = target.get(skill, 0.0) + share * part
    return target

def fit_softmax(rows: list[list[float]], targets: list[list[float]], strength: float, steps: int, rate: float) -> tuple[list[list[float]], list[float]]:
    width, classes, count = len(rows[0]), len(targets[0]), max(1, len(rows))
    weights = [[0.0] * width for _ in range(classes)]
    bias = [0.0] * classes
    for _ in range(steps):
        grad_w = [[0.0] * width for _ in range(classes)]
        grad_b = [0.0] * classes
        for row, target in zip(rows, targets):
            probabilities = _softmax([b + sum(w * x for w, x in zip(ws, row)) for ws, b in zip(weights, bias)])
            for k in range(classes):
                error = probabilities[k] - target[k]
                grad_b[k] += error
                line = grad_w[k]
                for j, x in enumerate(row):
                    line[j] += error * x
        for k in range(classes):
            bias[k] -= rate * grad_b[k] / count
            for j in range(width):
                weights[k][j] -= rate * (grad_w[k][j] / count + strength * weights[k][j])
    return weights, bias

def fit_binary(rows: list[list[float]], labels: list[float], weights_of: list[float], strength: float, steps: int, rate: float) -> tuple[list[float], float]:
    width, total = len(rows[0]), max(1e-9, sum(weights_of))
    weights, bias = [0.0] * width, 0.0
    for _ in range(steps):
        grad_w, grad_b = [0.0] * width, 0.0
        for row, label, weight in zip(rows, labels, weights_of):
            error = weight * (_sigmoid(bias + sum(w * x for w, x in zip(weights, row))) - label)
            grad_b += error
            for j, x in enumerate(row):
                grad_w[j] += error * x
        bias -= rate * grad_b / total
        for j in range(width):
            weights[j] -= rate * (grad_w[j] / total + strength * weights[j])
    return weights, bias

@dataclass
class Example:
    plain: dict[str, Any]
    views: dict[str, dict[str, Any]]
    slot: str
    set_id: int
    year: int
    own: bool = False
    secondary: Optional[str] = None

    def view(self, mod: str) -> dict[str, Any]:
        name = VIEW_OF[mod]
        return self.plain if name == "NF" else (self.views.get(name) or self.plain)

    def gates(self) -> tuple[float, float]:
        hr_cs = _get(self.views.get("NFHR") or {}, "effective.cs")
        return _get(self.plain, "effective.ar"), hr_cs if math.isfinite(hr_cs) else _get(self.plain, "effective.cs") * 1.3

@dataclass
class SkillModModel:
    skill_norm: Normaliser
    skill_weights: list[list[float]]
    skill_bias: list[float]
    mod_norm: Normaliser
    mod_weights: dict[str, list[float]]
    mod_bias: dict[str, float]
    classes: tuple[str, ...]
    version: str = VERSION

    def skill_row(self, features: dict[str, Any]) -> list[float]:
        z = self.skill_norm.apply(vector(features))
        return z + profile(z)

    @classmethod
    def fit(cls, examples: list[Example], strength: float = 0.01, steps: int = 300, rate: float = 0.5) -> "SkillModModel":
        known = [example for example in examples if example.slot in PARTS]
        skill_norm = Normaliser.fit([vector(example.view(PARTS[example.slot][0])) for example in known])

        def skill_row(features):
            z = skill_norm.apply(vector(features))
            return z + profile(z)

        rows = [skill_row(example.view(PARTS[example.slot][0])) for example in known]
        targets = [[skill_target(example.slot, example.secondary).get(skill, 0.0) for skill in SKILLS] for example in known]
        skill_weights, skill_bias = fit_softmax(rows, targets, strength, steps, rate)
        mod_norm = Normaliser.fit([seen_vector(example.plain, example.views) for example in known])
        mod_rows = [mod_norm.apply(seen_vector(example.plain, example.views)) for example in known]
        mod_weights, mod_bias = {}, {}
        for mod in MODS:
            labels, weighing = [], []
            for example in known:
                mods = {PARTS[example.slot][0]} | ({PARTS[example.secondary][0]} if example.secondary in PARTS else set())
                labels.append(1.0 if mod in mods else 0.0)
                weighing.append(1.0 if mod in mods else UNKNOWN_MOD_WEIGHT)
            if any(labels):
                mod_weights[mod], mod_bias[mod] = fit_binary(mod_rows, labels, weighing, strength, steps, rate)
        classes = tuple(sorted({example.slot for example in known} | set(SLOTS), key=lambda slot: (slot not in SLOTS, slot)))
        return cls(skill_norm, skill_weights, skill_bias, mod_norm, mod_weights, mod_bias, classes)

    def scores(self, plain: dict[str, Any], views: dict[str, dict[str, Any]], base_ar: float, hr_cs: float) -> dict[str, float]:
        example = Example(plain, views, "", 0, 0)
        skills: dict[str, list[float]] = {}
        mod_row = self.mod_norm.apply(seen_vector(plain, views))
        found = {}
        for slot in self.classes:
            mod, parts = PARTS[slot]
            if mod not in self.mod_weights or (slot == "HD2" and base_ar > HD2_MOST_AR) or (slot == "HR2" and hr_cs < HR2_LEAST_CS):
                continue
            view = VIEW_OF[mod]
            if view not in skills:
                row = self.skill_row(example.view(mod))
                skills[view] = _softmax([b + sum(w * x for w, x in zip(ws, row)) for ws, b in zip(self.skill_weights, self.skill_bias)])
            fits = _sigmoid(self.mod_bias[mod] + sum(w * x for w, x in zip(self.mod_weights[mod], mod_row)))
            found[slot] = sum(part * skills[view][SKILLS.index(skill)] for skill, part in parts.items()) * fits
        total = sum(found.values()) or 1.0
        return {slot: value / total for slot, value in found.items()}

    def predict(self, features: dict[str, Any], base_ar: float, hr_cs: float, views: Optional[dict[str, dict[str, Any]]] = None) -> Prediction:
        probabilities = self.scores(features, views or {}, base_ar, hr_cs)
        ranked = sorted(probabilities, key=lambda slot: -probabilities[slot])
        best = ranked[0] if ranked else None
        probability = probabilities.get(best, 0.0)
        margin = probability - (probabilities[ranked[1]] if len(ranked) > 1 else 0.0)
        outside = self.skill_norm.outside(vector(features))
        slot = best if best in SLOTS and not outside else None
        return Prediction(slot, probability, margin, probabilities, outside)

@dataclass
class SkillLayer:
    norm: Normaliser
    weights: list[list[float]]
    bias: list[float]

    @classmethod
    def fit(cls, examples: list[Example], strength: float, steps: int, rate: float) -> "SkillLayer":
        norm = Normaliser.fit([vector(example.plain) for example in examples])
        rows = [cls.row_of(norm, example.plain) for example in examples]
        targets = [[skill_target(example.slot, example.secondary).get(skill, 0.0) for skill in SKILLS] for example in examples]
        return cls(norm, *fit_softmax(rows, targets, strength, steps, rate))

    @staticmethod
    def row_of(norm: Normaliser, plain: dict[str, Any]) -> list[float]:
        z = norm.apply(vector(plain))
        return z + profile(z)

    def skills(self, plain: dict[str, Any]) -> list[float]:
        row = self.row_of(self.norm, plain)
        return _softmax([b + sum(w * x for w, x in zip(ws, row)) for ws, b in zip(self.weights, self.bias)])

    def both(self, plain: dict[str, Any], views: dict[str, dict[str, Any]]) -> list[float]:
        return self.skills(plain) + self.skills(views.get("NFDT") or plain)

STACK_FOLDS = 3

@dataclass
class StackedModel:
    layer: SkillLayer
    slots: SlotModel
    version: str = VERSION + "-stacked"

    @classmethod
    def fit(cls, examples: list[Example], strength: float = 0.01, steps: int = 300, rate: float = 0.5) -> "StackedModel":
        known = [example for example in examples if example.slot in PARTS]
        outside: dict[int, list[float]] = {}
        sets = sorted({example.set_id for example in known})
        for fold in range(STACK_FOLDS):
            held = {set_id for at, set_id in enumerate(sets) if at % STACK_FOLDS == fold}
            layer = SkillLayer.fit([example for example in known if example.set_id not in held], strength, steps, rate)
            for at, example in enumerate(known):
                if example.set_id in held:
                    outside[at] = layer.both(example.plain, example.views)
        rows = [seen_vector(example.plain, example.views) + outside[at] for at, example in enumerate(known)]
        named = tuple(f"skill:{skill}" for skill in SKILLS) + tuple(f"NFDT:skill:{skill}" for skill in SKILLS)
        slots = SlotModel.fit(rows, [example.slot for example in known], strength, steps, rate, paths=SEEN + named)
        return cls(SkillLayer.fit(known, strength, steps, rate), slots)

    def predict(self, features: dict[str, Any], base_ar: float, hr_cs: float, views: Optional[dict[str, dict[str, Any]]] = None) -> Prediction:
        raw = seen_vector(features, views) + self.layer.both(features, views or {})
        model = self.slots
        row = model.normaliser.apply(raw)
        scores = [b + sum(w * x for w, x in zip(ws, row)) for ws, b in zip(model.weights, model.bias)]
        allowed = [(name != "HD2" or base_ar <= HD2_MOST_AR) and (name != "HR2" or hr_cs >= HR2_LEAST_CS) for name in model.classes]
        probabilities = _softmax([score if ok else -math.inf for score, ok in zip(scores, allowed)])
        ranked = sorted(range(len(model.classes)), key=lambda k: -probabilities[k])
        best = ranked[0]
        second = next((k for k in ranked[1:] if model.classes[k] != OTHER), None)
        margin = probabilities[best] - (probabilities[second] if second is not None else 0.0)
        outside = model.normaliser.outside(raw)
        slot = model.classes[best] if model.classes[best] in SLOTS and not outside else None
        return Prediction(slot, probabilities[best], margin, dict(zip(model.classes, probabilities)), outside)

def decide(model, example: Example) -> Prediction:
    base_ar, hr_cs = example.gates()
    return model.predict(example.plain, base_ar, hr_cs, example.views)

def _auc(scores: list[float], positive: list[bool]) -> Optional[float]:
    good = sum(positive)
    bad = len(positive) - good
    if not good or not bad:
        return None
    ordered = sorted(zip(scores, positive), key=lambda pair: pair[0])
    ranks, at = 0.0, 0
    while at < len(ordered):
        end = at
        while end + 1 < len(ordered) and ordered[end + 1][0] == ordered[at][0]:
            end += 1
        shared = (at + end + 2) / 2
        ranks += shared * sum(1 for _, hit in ordered[at:end + 1] if hit)
        at = end + 1
    return (ranks - good * (good + 1) / 2) / (good * bad)

def signature_truth(examples: list[Example]) -> dict[str, Optional[float]]:
    known = [example for example in examples if example.slot in PARTS]
    if not known:
        return {}
    norm = Normaliser.fit([vector(example.plain) for example in known])
    profiles = [profile(norm.apply(vector(example.plain))) for example in known]
    return {skill: _auc([found[at] for found in profiles], [PARTS[example.slot][1].get(skill, 0.0) >= 0.5 for example in known])
            for at, skill in enumerate(SKILLS)}

def _scored(truth: list[str], predictions: list[Prediction], working: bool) -> dict[str, Any]:
    keep = [(t, p) for t, p in zip(truth, predictions) if not working or t in SLOTS]
    guessed = [max(p.probabilities, key=p.probabilities.get) if p.probabilities else None for _, p in keep]
    found = report([t for t, _ in keep], guessed)
    sure = [best == t for (t, p), best in zip(keep, guessed)
            if best not in (None, OTHER) and catalog.confidence_level(p.probability, p.margin) == "confident"]
    two = [t in sorted(p.probabilities, key=lambda slot: -p.probabilities[slot])[:2] for t, p in keep]
    found["confident"] = {"count": len(sure), "precision": sum(sure) / len(sure) if sure else 0.0, "share": len(sure) / len(keep) if keep else 0.0}
    found["top2"] = sum(two) / len(two) if two else 0.0
    return found

def compare(examples: list[Example], steps: int = 300, report_to: Callable[[str], None] = print) -> dict[str, Any]:
    truth: list[str] = []
    old: list[Prediction] = []
    new: list[Prediction] = []
    stacked: list[Prediction] = []
    for year in sorted({example.year for example in examples if not example.own}):
        held = [example for example in examples if example.year == year and not example.own]
        sets = {example.set_id for example in held}
        rest = [example for example in examples if example.set_id not in sets and not (example.year == year and not example.own)]
        baseline = SlotModel.fit([seen_vector(example.plain, example.views) for example in rest], [example.slot for example in rest], 0.01, steps, paths=SEEN)
        layered = SkillModModel.fit(rest, steps=steps)
        stack = StackedModel.fit(rest, steps=steps)
        for example in held:
            truth.append(example.slot)
            old.append(decide(baseline, example))
            new.append(decide(layered, example))
            stacked.append(decide(stack, example))
        report_to(f"held out {year}: {len(held)} maps")
    return {"working": {"current": _scored(truth, old, True), "skill_mod": _scored(truth, new, True), "stacked": _scored(truth, stacked, True)},
            "catalogue": {"current": _scored(truth, old, False), "skill_mod": _scored(truth, new, False), "stacked": _scored(truth, stacked, False)},
            "signatures": signature_truth(examples)}

async def corpus_examples(factory) -> list[Example]:
    from sqlalchemy import select

    from db.models.duel_catalog import DuelTrainingExample
    from services.duels import corpus, importer

    async with factory() as session:
        labels = await importer.corpus_labels(session)
        rows = (await session.scalars(select(DuelTrainingExample).where(
            DuelTrainingExample.corpus_version.in_(corpus.LEARNED), DuelTrainingExample.conflict.is_(False),
            DuelTrainingExample.revision_hash.is_not(None)))).all()
        found = []
        for row in rows:
            label, _ = importer.resolved(row, labels)
            if label not in PARTS:
                continue
            plain, views = await importer.seen_of(session, row.revision_hash)
            if plain:
                found.append(Example(plain, views, label, int(row.beatmapset_id or row.beatmap_id), row.year, row.corpus_version == corpus.OWN_VERSION))
    return found
